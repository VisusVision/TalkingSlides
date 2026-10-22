"""Pure contract tests; runnable with unittest without Django/pytest plugins."""
import copy
import json
from pathlib import Path
import unittest
from core.visual_design import DesignError, DeterministicPlanner, SlideSourceSnapshot, VisualPlan, VisualPreview, render_preview

FIXTURES = Path(__file__).parent / "fixtures" / "visual_design"


def fixture(name="process"):
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


class ContractTests(unittest.TestCase):
    def test_zero_and_split_identity_round_trip(self):
        snapshot = SlideSourceSnapshot(fixture())
        self.assertEqual(snapshot.identity["source_slide_index"], 0)
        self.assertEqual(snapshot.identity["split_index"], 1)
        self.assertEqual(SlideSourceSnapshot.from_json(snapshot.to_json()), snapshot)

    def test_unknown_fields_at_every_layer(self):
        for target in ("source", "evidence", "data", "step", "neighbor"):
            with self.subTest(target=target):
                raw = fixture()
                if target == "neighbor":
                    raw["neighboring_context"] = [{"project_id": raw["project_id"], "page_key": "neighbor", "source_revision": raw["source_revision"], "display_text": "Context"}]
                obj = {"source": raw, "evidence": raw["evidence"][0], "data": raw["evidence"][0]["data"], "step": raw["evidence"][0]["data"]["steps"][0], "neighbor": raw.get("neighboring_context", [{}])[0]}[target]
                obj["unexpected"] = True
                with self.assertRaisesRegex(DesignError, "Unknown fields"):
                    SlideSourceSnapshot(raw)

    def test_missing_fields_are_not_invented(self):
        for key in fixture():
            raw = fixture()
            del raw[key]
            with self.subTest(key=key), self.assertRaises(DesignError):
                SlideSourceSnapshot(raw)

    def test_invalid_indices(self):
        for value in (None, -1, True, "0", 1.0):
            for key in ("source_slide_index", "split_index"):
                raw = fixture()
                raw[key] = value
                with self.subTest(value=value, key=key), self.assertRaises(DesignError):
                    SlideSourceSnapshot(raw)

    def test_conflicting_revision_and_neighbor_identity(self):
        raw = fixture()
        raw["evidence"][0]["source_revision"] = "sha256:" + "0" * 64
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(raw)
        for project, page in (("other-project", "next"), ("synthetic-lesson", fixture()["page_key"])):
            raw = fixture()
            raw["neighboring_context"] = [{"project_id": project, "page_key": page, "source_revision": raw["source_revision"], "display_text": "Neighbor"}]
            with self.assertRaises(DesignError):
                SlideSourceSnapshot(raw)

    def test_duplicate_evidence_and_invalid_process_references(self):
        raw = fixture()
        raw["evidence"] *= 2
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(raw)
        for order in (["collect", "filter", "unknown"], ["collect", "collect", "store"]):
            raw = fixture()
            raw["evidence"][0]["data"]["order"] = order
            with self.assertRaises(DesignError):
                SlideSourceSnapshot(raw)

    def test_unsupported_evidence_and_relationships(self):
        raw = fixture()
        raw["evidence"][0]["kind"] = "scientific_illustration"
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(raw)
        raw = fixture()
        raw["evidence"][0]["data"]["relationship"] = "causes"
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(raw)

    def test_numeric_contract_rejects_nonfinite_bool_and_missing_unit(self):
        for value in (float("nan"), float("inf"), -float("inf"), True, "12", 10**100):
            raw = fixture("numerical_chart")
            raw["evidence"][0]["data"]["points"][0]["value"] = value
            with self.subTest(value=value), self.assertRaises(DesignError):
                SlideSourceSnapshot(raw)
        raw = fixture("numerical_chart")
        del raw["evidence"][0]["data"]["unit"]
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(raw)

    def test_incomplete_comparison_and_duplicate_labels(self):
        raw = fixture("comparison")
        raw["evidence"][0]["data"]["items"][0]["values"].pop()
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(raw)
        raw = fixture("numerical_chart")
        points = raw["evidence"][0]["data"]["points"]
        points[1]["label"] = points[0]["label"]
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(raw)

    def test_asset_references_are_opaque(self):
        for reference in ("https://example.com/image.svg", "../../file", "data:image/svg+xml;base64,abc"):
            raw = fixture()
            raw["original_asset_ref"] = reference
            with self.assertRaises(DesignError):
                SlideSourceSnapshot(raw)

    def test_unsafe_markup_and_controls_rejected_but_xml_text_allowed(self):
        for value in ('<script>alert(1)</script>', '<img src="x" onerror="x">', 'javascript:alert(1)', 'x\x00y', 'x\u202ey', 'onload=run()'):
            raw = fixture()
            raw["original_display_text"] = value
            with self.subTest(value=value), self.assertRaises(DesignError):
                SlideSourceSnapshot(raw)
        raw = fixture()
        raw["original_display_text"] = 'İğne & ölçüm: 12 < 18, "şüphe"'
        self.assertEqual(SlideSourceSnapshot(raw).to_dict()["original_display_text"], raw["original_display_text"])

    def test_size_and_json_duplicate_limits(self):
        raw = fixture()
        raw["original_display_text"] = "A" * 70000
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(raw)
        with self.assertRaises(DesignError):
            SlideSourceSnapshot.from_json('{"schema_version":"slide-source.v1","schema_version":"slide-source.v1"}')
        with self.assertRaises(DesignError):
            SlideSourceSnapshot.from_json("[" * 20 + "0" + "]" * 20)

    def test_hashing_is_canonical_versioned_and_sensitive(self):
        raw = fixture()
        snapshot = SlideSourceSnapshot(raw)
        reordered = dict(reversed(list(raw.items())))
        self.assertEqual(snapshot.content_hash, SlideSourceSnapshot(reordered).content_hash)
        self.assertTrue(snapshot.content_hash.startswith("sha256:"))
        for key, replacement in (("protected_narration", "Changed narration"), ("original_display_text", "Changed display"), ("page_key", "different-page"), ("source_revision", "sha256:" + "0" * 64)):
            changed = copy.deepcopy(raw)
            changed[key] = replacement
            if key == "source_revision":
                changed["evidence"][0][key] = replacement
            self.assertNotEqual(snapshot.content_hash, SlideSourceSnapshot(changed).content_hash)
        changed = fixture()
        changed["schema_version"] = "slide-source.v2"
        with self.assertRaises(DesignError):
            SlideSourceSnapshot(changed)

    def test_unsupported_unicode_returns_contract_error(self):
        for label in ("Unicode \ud800", "Unsupported \U0001f600"):
            raw = fixture()
            raw["original_display_text"] = label
            with self.assertRaises(DesignError):
                SlideSourceSnapshot(raw)

    def test_contracts_do_not_expose_mutable_state(self):
        raw = fixture()
        snapshot = SlideSourceSnapshot(raw)
        before = snapshot.to_json()
        raw["evidence"][0]["data"]["steps"][0]["label"] = "Changed"
        snapshot.to_dict()["evidence"].clear()
        self.assertEqual(snapshot.to_json(), before)
        with self.assertRaises(AttributeError):
            snapshot._json = "{}"

    def test_preview_deserialization_checks_svg_safety(self):
        source = SlideSourceSnapshot(fixture())
        preview = render_preview(source, DeterministicPlanner().plan(source)).to_dict()
        preview["svg"] = preview["svg"].replace("</svg>", "<script>alert(1)</script></svg>")
        with self.assertRaises(DesignError):
            VisualPreview(preview)

    def test_plan_and_preview_reject_unknown_fields_operations_versions(self):
        snapshot = SlideSourceSnapshot(fixture())
        plan = DeterministicPlanner().plan(snapshot)
        for field, val in (("extra", 1), ("strategy", "arbitrary_svg"), ("schema_version", "visual-plan.v2"), ("template_version", "unknown")):
            raw = plan.to_dict()
            raw[field] = val
            with self.subTest(field=field), self.assertRaises(DesignError):
                VisualPlan(raw)
        raw = plan.to_dict()
        raw["layout"]["svg"] = "<svg/>"
        with self.assertRaises(DesignError):
            VisualPlan(raw)
        preview = render_preview(snapshot, plan)
        for field, val in (("extra", 1), ("renderer_version", "unknown.v9"), ("width", 1600), ("contains_text", "yes"), ("errors", ["failed"])):
            raw = preview.to_dict()
            raw[field] = val
            with self.subTest(field=field), self.assertRaises(DesignError):
                VisualPreview(raw)
        self.assertEqual(VisualPlan.from_json(plan.to_json()), plan)
        self.assertEqual(VisualPreview.from_json(preview.to_json()), preview)


if __name__ == "__main__":
    unittest.main()
