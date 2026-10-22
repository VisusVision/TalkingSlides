import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from core.visual_design import DeterministicPlanner, SlideSourceSnapshot, VisualPlan, render_preview, validate_plan

FIXTURES = Path(__file__).parent / "fixtures" / "visual_design"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


class PlannerTests(unittest.TestCase):
    def test_five_strategies_and_repeatability(self):
        expected = {"process": "flowchart", "comparison": "comparison", "numerical_chart": "chart", "preserve_original": "preserve_original", "insufficient_evidence": "typography_only"}
        for name, strategy in expected.items():
            with self.subTest(name=name):
                snapshot = SlideSourceSnapshot(fixture(name))
                a = DeterministicPlanner().plan(snapshot)
                b = DeterministicPlanner().plan(snapshot)
                self.assertEqual(a.to_json(), b.to_json())
                self.assertEqual(a.content_hash, b.content_hash)
                self.assertEqual(a.to_dict()["strategy"], strategy)
                self.assertTrue(validate_plan(snapshot, a).valid)

    def test_narration_and_input_immutability(self):
        raw = fixture("process")
        snapshot = SlideSourceSnapshot(raw)
        before = snapshot.to_json()
        plan = DeterministicPlanner().plan(snapshot)
        preview = render_preview(snapshot, plan)
        self.assertEqual(snapshot.to_json(), before)
        self.assertEqual(snapshot.to_dict()["protected_narration"], raw["protected_narration"])
        self.assertNotIn(raw["protected_narration"], preview.to_dict()["svg"])
        self.assertNotIn("protected_narration", plan.to_dict())

    def test_process_uses_explicit_order_not_array_order(self):
        raw = fixture("process")
        raw["evidence"][0]["data"]["steps"].reverse()
        plan = DeterministicPlanner().plan(SlideSourceSnapshot(raw)).to_dict()
        self.assertEqual(plan["layout"]["order"], ["collect", "filter", "store"])
        self.assertEqual(plan["required_labels"][1], "Ölçümü kaydet & tarihi ekle")
        self.assertEqual(plan["layout"]["relationship"], "sequence")

    def test_prose_is_not_structured_evidence(self):
        for prose in ("B is better.", "First A, then B.", "A causes B.", "A = 12 and B = 18."):
            raw = fixture("insufficient_evidence")
            raw["original_display_text"] = prose
            plan = DeterministicPlanner().plan(SlideSourceSnapshot(raw)).to_dict()
            self.assertEqual(plan["strategy"], "typography_only")
            self.assertEqual(plan["layout"], {"text": prose})
            self.assertIsNotNone(plan["fallback_reason"])

    def test_unverified_and_ambiguous_evidence_fallback(self):
        raw = fixture("process")
        raw["evidence"][0]["verified"] = False
        self.assertEqual(DeterministicPlanner().plan(SlideSourceSnapshot(raw)).to_dict()["strategy"], "typography_only")
        raw = fixture("process")
        other = json.loads(json.dumps(raw["evidence"][0]))
        other["id"] = "second-record"
        raw["evidence"].append(other)
        self.assertEqual(DeterministicPlanner().plan(SlideSourceSnapshot(raw)).to_dict()["strategy"], "typography_only")

    def test_original_asset_and_empty_source_fallback(self):
        raw = fixture("insufficient_evidence")
        raw["original_asset_ref"] = "asset:original"
        self.assertEqual(DeterministicPlanner().plan(SlideSourceSnapshot(raw)).to_dict()["strategy"], "preserve_original")
        raw.pop("original_asset_ref")
        raw["original_display_text"] = ""
        source = SlideSourceSnapshot(raw)
        preview = render_preview(source, DeterministicPlanner().plan(source)).to_dict()
        self.assertEqual(preview["status"], "preserved")
        self.assertTrue(preview["warnings"])

    def test_chart_domain_fallback_preserves_evidence(self):
        for values in ((-12, 18), (0, 0)):
            raw = fixture("numerical_chart")
            for point, value in zip(raw["evidence"][0]["data"]["points"], values):
                point["value"] = value
            source = SlideSourceSnapshot(raw)
            plan = DeterministicPlanner().plan(source).to_dict()
            self.assertEqual(plan["strategy"], "typography_only")
            self.assertIn("numeric domain", plan["fallback_reason"])
            self.assertEqual([p["value"] for p in source.to_dict()["evidence"][0]["data"]["points"]], list(values))

    def test_semantic_tampering_rejected_independently(self):
        for name in ("process", "comparison", "numerical_chart"):
            source = SlideSourceSnapshot(fixture(name))
            raw = DeterministicPlanner().plan(source).to_dict()
            if name == "process":
                raw["layout"]["order"].reverse()
            elif name == "comparison":
                raw["layout"]["items"][0]["values"][0] = "Invented fact"
            else:
                raw["layout"]["points"][0]["value"] = 999
            with self.subTest(name=name):
                plan = VisualPlan(raw)
                self.assertFalse(validate_plan(source, plan).valid)
                result = render_preview(source, plan).to_dict()
                self.assertEqual(result["status"], "invalid")
                self.assertIsNone(result["svg"])

    def test_units_labels_required_content_and_references(self):
        source = SlideSourceSnapshot(fixture("numerical_chart"))
        plan = DeterministicPlanner().plan(source).to_dict()
        for change in ("unit", "label", "required", "reference"):
            raw = json.loads(json.dumps(plan))
            if change == "unit":
                raw["layout"]["unit"] = "kg"
            elif change == "label":
                raw["layout"]["points"][0]["label"] = "Other group"
            elif change == "required":
                raw["required_labels"].pop()
            else:
                raw["evidence_refs"] = ["missing-evidence"]
            self.assertFalse(validate_plan(source, VisualPlan(raw)).valid, change)

    def test_source_edit_and_identity_changes_invalidate_plan(self):
        original = SlideSourceSnapshot(fixture("process"))
        plan = DeterministicPlanner().plan(original)
        for key, replacement in (("page_key", "different-page"), ("project_id", "other-project"), ("source_slide_index", 2), ("split_index", 0), ("protected_narration", "New narration"), ("original_display_text", "New title")):
            raw = original.to_dict()
            raw[key] = replacement
            with self.subTest(key=key):
                self.assertFalse(validate_plan(SlideSourceSnapshot(raw), plan).valid)
        raw = plan.to_dict()
        raw["source_identity"]["page_key"] = "other-page"
        self.assertFalse(validate_plan(original, VisualPlan(raw)).valid)

    def test_neighbor_context_is_not_evidence(self):
        raw = fixture("insufficient_evidence")
        raw["neighboring_context"] = [{"project_id": raw["project_id"], "page_key": "next", "source_revision": raw["source_revision"], "display_text": "A has 12, B has 18"}]
        plan = DeterministicPlanner().plan(SlideSourceSnapshot(raw)).to_dict()
        self.assertEqual(plan["strategy"], "typography_only")
        self.assertEqual(plan["evidence_refs"], [])

    def test_baseline_makes_no_network_calls(self):
        with patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")), patch("urllib.request.urlopen", side_effect=AssertionError("HTTP forbidden")):
            for path in FIXTURES.glob("*.json"):
                source = SlideSourceSnapshot.from_json(path.read_text(encoding="utf-8"))
                result = render_preview(source, DeterministicPlanner().plan(source))
                self.assertIn(result.to_dict()["status"], ("valid", "preserved"))


if __name__ == "__main__":
    unittest.main()
