from collections import Counter
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from core.visual_design import DeterministicPlanner, SlideSourceSnapshot, render_preview, validate_svg

FIXTURES = Path(__file__).parent / "fixtures" / "visual_design"
PREVIEWS = Path(__file__).parents[1] / "docs" / "visual_design_phase1" / "previews"
NS = {"s": "http://www.w3.org/2000/svg"}


def generate(name):
    source = SlideSourceSnapshot.from_json((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))
    plan = DeterministicPlanner().plan(source)
    return source, plan, render_preview(source, plan)


class SvgTests(unittest.TestCase):
    def test_byte_repeatability_and_xml_parsing(self):
        for name in ("process", "comparison", "numerical_chart", "insufficient_evidence"):
            source, plan, preview = generate(name)
            raw = preview.to_dict()
            with self.subTest(name=name):
                self.assertEqual(raw["status"], "valid", raw["errors"])
                other = render_preview(source, plan)
                self.assertEqual(preview.to_json(), other.to_json())
                self.assertEqual(preview.content_hash, other.content_hash)
                self.assertEqual(raw["svg"].encode(), other.to_dict()["svg"].encode())
                self.assertEqual(ET.fromstring(raw["svg"]).attrib["viewBox"], "0 0 1920 1080")
                self.assertTrue(raw["contains_text"])
                self.assertTrue(validate_svg(raw["svg"], plan).valid)
                self.assertEqual(raw["source_hash"], source.content_hash)
                self.assertEqual(raw["plan_hash"], plan.content_hash)

    def test_exact_required_labels_and_xml_escaping(self):
        for name in ("process", "comparison", "numerical_chart"):
            _, plan, preview = generate(name)
            root = ET.fromstring(preview.to_dict()["svg"])
            labels = [e.attrib["aria-label"] for e in root.findall("s:text", NS)]
            self.assertEqual(Counter(labels), Counter(plan.to_dict()["required_labels"]))
        self.assertIn("&amp;", generate("process")[2].to_dict()["svg"])
        self.assertIn("&lt;", generate("numerical_chart")[2].to_dict()["svg"])
        self.assertIn("İ", generate("comparison")[2].to_dict()["svg"])

    def test_meaningful_geometry_and_exact_chart_values(self):
        _, plan, preview = generate("process")
        root = ET.fromstring(preview.to_dict()["svg"])
        self.assertEqual(len(root.findall("s:polygon", NS)), 2)
        self.assertEqual([e.attrib["aria-label"] for e in root.findall("s:text", NS)], plan.to_dict()["required_labels"])
        root = ET.fromstring(generate("comparison")[2].to_dict()["svg"])
        self.assertEqual(len(root.findall("s:text", NS)), 9)
        root = ET.fromstring(generate("numerical_chart")[2].to_dict()["svg"])
        bars = [e for e in root.findall("s:rect", NS) if "data-point" in e.attrib]
        self.assertEqual([float(b.attrib["width"]) for b in bars], [704, 1056])
        self.assertEqual([b.attrib["x"] for b in bars], ["560", "560"])
        labels = [e.attrib["aria-label"] for e in root.findall("s:text", NS)]
        for exact in ("12", "18", "fidan"):
            self.assertIn(exact, labels)

    def test_preserve_has_no_invented_svg(self):
        raw = generate("preserve_original")[2].to_dict()
        self.assertEqual(raw["status"], "preserved")
        self.assertIsNone(raw["svg"])
        self.assertFalse(raw["contains_text"])

    def test_active_external_and_unsupported_operations(self):
        _, plan, preview = generate("process")
        svg = preview.to_dict()["svg"]
        attacks = (svg.replace("</svg>", '<script>alert(1)</script></svg>'), svg.replace('<rect ', '<rect onload="alert(1)" ', 1), svg.replace('fill="#ffffff"', 'fill="url(https://evil.test/x)"'), svg.replace('</svg>', '<foreignObject/></svg>'), '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///x">]>' + svg)
        for attack in attacks:
            self.assertFalse(validate_svg(attack, plan).valid)
        for name in ("process", "comparison", "numerical_chart", "insufficient_evidence"):
            self.assertNotRegex(generate(name)[2].to_dict()["svg"], r"(?i)<script|foreignObject|onload|href=|url\(|<!|<\?")

    def test_independent_validation_of_labels_values_geometry(self):
        _, plan, preview = generate("numerical_chart")
        svg = preview.to_dict()["svg"]
        attacks = (svg.replace('width="704"', 'width="900"'), svg.replace('aria-label="12"', 'aria-label="13"'), svg.replace('>12</tspan>', '>13</tspan>'), svg.replace('width="704"', 'width="NaN"'), svg.replace('x="560"', 'x="-20"'), svg.replace('data-point="0"', 'data-point="9"'))
        for attack in attacks:
            self.assertNotEqual(attack, svg)
            self.assertFalse(validate_svg(attack, plan).valid)

    def test_required_text_never_silently_truncates(self):
        for label in ("A" * 180, "many short words " * 12):
            raw = json.loads((FIXTURES / "process.json").read_text(encoding="utf-8"))
            raw["evidence"][0]["data"]["steps"][0]["label"] = label
            source = SlideSourceSnapshot(raw)
            result = render_preview(source, DeterministicPlanner().plan(source)).to_dict()
            self.assertEqual(result["status"], "invalid")
            self.assertIsNone(result["svg"])
            self.assertIn("text_overflow", result["errors"][0])

    def test_step_template_limit(self):
        raw = json.loads((FIXTURES / "process.json").read_text(encoding="utf-8"))
        for number in (4, 5):
            raw["evidence"][0]["data"]["steps"].append({"id": f"step-{number}", "label": "Next verified step"})
            raw["evidence"][0]["data"]["order"].append(f"step-{number}")
        source = SlideSourceSnapshot(raw)
        result = render_preview(source, DeterministicPlanner().plan(source)).to_dict()
        self.assertEqual(result["status"], "invalid")
        self.assertIn("layout_limit", result["errors"][0])

    def test_comparison_facts_cannot_swap_columns(self):
        _, plan, preview = generate("comparison")
        svg = preview.to_dict()["svg"]
        swapped = svg.replace("İletim", "TEMP").replace("Işınım", "İletim").replace("TEMP", "Işınım")
        self.assertFalse(validate_svg(swapped, plan).valid)

    def test_sequence_arrow_direction_is_validated(self):
        _, plan, preview = generate("process")
        root = ET.fromstring(preview.to_dict()["svg"])
        arrow = root.find("s:polygon", NS)
        arrow.attrib["points"] = "100,626 88,638 100,650"
        self.assertFalse(validate_svg(ET.tostring(root, encoding="unicode"), plan).valid)

    def test_extreme_chart_dynamic_range_reports_resolution_error(self):
        raw = json.loads((FIXTURES / "numerical_chart.json").read_text(encoding="utf-8"))
        raw["evidence"][0]["data"]["points"][0]["value"] = 0.00000001
        source = SlideSourceSnapshot(raw)
        result = render_preview(source, DeterministicPlanner().plan(source)).to_dict()
        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(result["svg"])
        self.assertIn("chart_resolution", result["errors"][0])

    def test_resource_and_out_of_canvas_text_limits(self):
        _, plan, preview = generate("process")
        svg = preview.to_dict()["svg"]
        self.assertFalse(validate_svg("x" * 50001, plan).valid)
        self.assertFalse(validate_svg(svg.replace('y="520"', 'y="1080"'), plan).valid)
        self.assertFalse(validate_svg(svg.replace('</svg>', '<image href="https://example.com/x"/></svg>'), plan).valid)

    def test_committed_artifacts_and_manifest(self):
        manifest = json.loads((PREVIEWS / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["examples"]), 5)
        for example in manifest["examples"]:
            _, plan, preview = generate(example["fixture"])
            raw = preview.to_dict()
            self.assertEqual(example["strategy"], raw["strategy"])
            self.assertEqual(example["validation"], raw["status"])
            self.assertEqual(example["plan_hash"], plan.content_hash)
            self.assertEqual(example["preview_hash"], preview.content_hash)
            if example["svg_file"]:
                self.assertEqual((PREVIEWS / example["svg_file"]).read_text(encoding="utf-8"), raw["svg"])
            else:
                self.assertIsNone(raw["svg"])


if __name__ == "__main__":
    unittest.main()
