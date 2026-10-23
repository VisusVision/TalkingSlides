"""Strict, immutable JSON contracts. No Django or provider dependencies."""
from dataclasses import dataclass
import hashlib
import json
import math
import re
import unicodedata

SOURCE_VERSION = "slide-source.v1"
PLAN_VERSION = "visual-plan.v1"
PREVIEW_VERSION = "visual-preview.v1"
TEMPLATE_VERSION = "educational.v1"
RENDERER_VERSION = "svg-educational.v2"
STRATEGIES = {"preserve_original", "typography_only", "flowchart", "comparison", "chart"}
MAX_PAYLOAD_BYTES = 65_536


class DesignError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise DesignError(code, message)


def canonical_json(value):
    """Versioned hashes use exact Unicode, sorted keys and finite JSON numbers."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(domain, value):
    encoded = ("visual-design-hash.v1\n" + domain + "\n" + canonical_json(value)).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _budget(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        require(count <= 4000 and depth <= 12, "payload_limit", "Payload nesting/node limit exceeded")
        require(type(item) in (dict, list, str, int, float, bool, type(None)), "json_type", "Only JSON types are supported")
        if isinstance(item, dict):
            require(all(type(k) is str for k in item), "json_type", "Object keys must be strings")
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif type(item) is int:
            require(abs(item) <= 10**15, "number", "Integer outside resource bounds")
        elif type(item) is float:
            require(math.isfinite(item), "nonfinite_number", "Nonfinite numbers are forbidden")
        elif type(item) is str:
            require(len(item) <= MAX_PAYLOAD_BYTES, "payload_limit", "String too large")
    try:
        size = len(canonical_json(value).encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise DesignError("unsafe_text", "Invalid Unicode scalar value") from exc
    require(size <= MAX_PAYLOAD_BYTES, "payload_limit", "Payload too large")


def fields(value, required, optional=()):
    require(type(value) is dict, "schema", "Expected object")
    require(set(required) <= value.keys(), "missing_field", "Missing fields: " + str(sorted(set(required) - value.keys())))
    require(value.keys() <= set(required) | set(optional), "unknown_field", "Unknown fields: " + str(sorted(value.keys() - set(required) - set(optional))))


def text(value, maximum=2000, empty=False):
    require(type(value) is str and len(value) <= maximum and (empty or bool(value.strip())), "text", "Invalid text length or type")
    # English/Turkish Latin text and ordinary punctuation only in this prototype.
    for char in value:
        cp = ord(char)
        supported = char in "\n\tÇĞİÖŞÜçğıöşü\u00a0‘’“”–—…•" or 32 <= cp <= 126
        require(supported and (char in "\n\t" or unicodedata.category(char)[0] != "C"), "unsafe_text", "Unsupported character or control in text")
    require(not re.search(r"<\s*(?:/?\s*[a-zA-Z][^>]*|![^>]*|\?[^>]*)>", value), "unsafe_text", "Markup is not accepted as authored text")
    require(not re.search(r"(?:javascript\s*:|data\s*:|expression\s*\(|\bon\w+\s*=)", value, re.I), "unsafe_text", "Executable text syntax is not accepted")
    return value


def identifier(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is not None, "identity", "Expected opaque identity")


def digest(value):
    require(type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None, "hash", "Expected versioned SHA-256 digest")


def index(value):
    require(type(value) is int and 0 <= value <= 1_000_000, "index", "Index must be a nonnegative integer (zero is valid)")


def array(value, minimum=0, maximum=24):
    require(type(value) is list and minimum <= len(value) <= maximum, "array_limit", "Invalid array type or size")


def unique(values):
    require(len(set(values)) == len(values), "duplicate", "Duplicate identities/labels are ambiguous")


def identity(value):
    fields(value, ("project_id", "page_key", "source_slide_index", "split_index", "source_revision"))
    identifier(value["project_id"])
    identifier(value["page_key"])
    index(value["source_slide_index"])
    index(value["split_index"])
    digest(value["source_revision"])


def evidence_record(record, revision):
    fields(record, ("id", "kind", "source_revision", "locator", "verified", "data"), ("heading",))
    identifier(record["id"])
    require(record["source_revision"] == revision, "source_revision", "Evidence revision does not match source")
    text(record["locator"], 256)
    if "heading" in record:
        text(record["heading"], 256)
    require(type(record["verified"]) is bool, "schema", "verified must be a boolean assertion by the source adapter")
    kind, data = record["kind"], record["data"]
    require(type(kind) is str and kind in {"process", "comparison", "numeric", "preserve"}, "evidence_type", "Unsupported evidence kind")
    if kind == "process":
        fields(data, ("steps", "order", "relationship"))
        require(data["relationship"] == "sequence", "relationship", "Only explicit sequence relationships are supported")
        array(data["steps"], 2, 6)
        for step in data["steps"]:
            fields(step, ("id", "label"))
            identifier(step["id"])
            text(step["label"], 240)
        ids = [s["id"] for s in data["steps"]]
        unique(ids)
        array(data["order"], len(ids), len(ids))
        require(all(type(x) is str for x in data["order"]), "reference", "Process references must be strings")
        require(set(data["order"]) == set(ids) and len(set(data["order"])) == len(ids), "reference", "Order must reference every step exactly once")
    elif kind == "comparison":
        fields(data, ("categories", "items"))
        array(data["categories"], 1, 4)
        for category in data["categories"]:
            text(category, 100)
        unique(data["categories"])
        array(data["items"], 2, 2)
        for item in data["items"]:
            fields(item, ("label", "values"))
            text(item["label"], 100)
            array(item["values"], len(data["categories"]), len(data["categories"]))
            for value in item["values"]:
                text(value, 180)
        unique([i["label"] for i in data["items"]])
    elif kind == "numeric":
        fields(data, ("measure", "unit", "points"))
        text(data["measure"], 120)
        text(data["unit"], 60)
        array(data["points"], 2, 6)
        for point in data["points"]:
            fields(point, ("label", "value"))
            text(point["label"], 120)
            value = point["value"]
            require(type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e12, "number", "Expected finite bounded numeric evidence")
        unique([p["label"] for p in data["points"]])
    else:
        fields(data, ("reason",))
        text(data["reason"], 500)


def layout_fields(strategy, layout):
    expected = {
        "preserve_original": ("asset_ref",),
        "typography_only": ("text",),
        "flowchart": ("heading", "evidence_id", "steps", "order", "relationship"),
        "comparison": ("heading", "evidence_id", "categories", "items"),
        "chart": ("heading", "evidence_id", "measure", "unit", "points"),
    }
    fields(layout, expected[strategy])
    if strategy == "preserve_original":
        asset_ref(layout["asset_ref"])
    elif strategy == "typography_only":
        text(layout["text"], empty=True)
    else:
        text(layout["heading"], empty=True)
        identifier(layout["evidence_id"])
        payload = {k: v for k, v in layout.items() if k not in {"heading", "evidence_id"}}
        kind = {"flowchart": "process", "comparison": "comparison", "chart": "numeric"}[strategy]
        evidence_record({"id": layout["evidence_id"], "kind": kind, "source_revision": "plan", "locator": "plan", "verified": True, "data": payload}, "plan")


def asset_ref(value):
    require(value is None or (type(value) is str and re.fullmatch(r"asset:[A-Za-z0-9_.-]{1,128}", value) is not None), "asset_reference", "Only opaque asset: IDs are accepted, never URLs or paths")


@dataclass(frozen=True, init=False)
class _Contract:
    _json: str

    def __init__(self, value):
        _budget(value)
        self._validate(value)
        object.__setattr__(self, "_json", canonical_json(value))

    @classmethod
    def from_dict(cls, value):
        return cls(value)

    @classmethod
    def from_json(cls, value):
        require(type(value) is str and len(value) <= MAX_PAYLOAD_BYTES, "payload_limit", "JSON too large")
        try:
            require(len(value.encode("utf-8")) <= MAX_PAYLOAD_BYTES, "payload_limit", "JSON too large")
        except UnicodeEncodeError as exc:
            raise DesignError("unsafe_text", "Invalid Unicode scalar value") from exc
        def pairs(items):
            result = {}
            for key, val in items:
                require(key not in result, "duplicate_field", "Duplicate JSON key: " + key)
                result[key] = val
            return result
        try:
            return cls(json.loads(value, object_pairs_hook=pairs))
        except (json.JSONDecodeError, RecursionError) as exc:
            raise DesignError("json", "Invalid JSON") from exc

    def to_dict(self):
        return json.loads(self._json)

    def to_json(self):
        return self._json

    @property
    def content_hash(self):
        return content_hash(self.to_dict()["schema_version"], self.to_dict())


class SlideSourceSnapshot(_Contract):
    __slots__ = ()

    def _validate(self, value):
        fields(value, ("schema_version", "project_id", "page_key", "source_slide_index", "split_index", "original_display_text", "protected_narration", "source_revision", "evidence"), ("original_asset_ref", "neighboring_context"))
        require(value["schema_version"] == SOURCE_VERSION, "version", "Unsupported source schema")
        identity({k: value[k] for k in ("project_id", "page_key", "source_slide_index", "split_index", "source_revision")})
        text(value["original_display_text"], empty=True)
        text(value["protected_narration"], 8000, empty=True)
        asset_ref(value.get("original_asset_ref"))
        array(value["evidence"], 0, 12)
        for record in value["evidence"]:
            evidence_record(record, value["source_revision"])
        unique([r["id"] for r in value["evidence"]])
        neighbors = value.get("neighboring_context", [])
        array(neighbors, 0, 2)
        for neighbor in neighbors:
            fields(neighbor, ("project_id", "page_key", "source_revision", "display_text"))
            identifier(neighbor["page_key"])
            digest(neighbor["source_revision"])
            text(neighbor["display_text"], empty=True)
            require(neighbor["project_id"] == value["project_id"] and neighbor["page_key"] != value["page_key"], "identity", "Conflicting neighboring page identity")
        unique([n["page_key"] for n in neighbors])

    @property
    def identity(self):
        data = self.to_dict()
        return {k: data[k] for k in ("project_id", "page_key", "source_slide_index", "split_index", "source_revision")}


class VisualPlan(_Contract):
    __slots__ = ()

    def _validate(self, value):
        fields(value, ("schema_version", "source_identity", "source_hash", "strategy", "rationale", "layout", "required_labels", "evidence_refs", "template_version", "fallback_reason"))
        require(value["schema_version"] == PLAN_VERSION and value["template_version"] == TEMPLATE_VERSION, "version", "Unsupported plan/template schema")
        identity(value["source_identity"])
        digest(value["source_hash"])
        require(type(value["strategy"]) is str and value["strategy"] in STRATEGIES, "operation", "Unsupported visual strategy")
        text(value["rationale"], 1000)
        require(value["fallback_reason"] is None or type(value["fallback_reason"]) is str, "schema", "Invalid fallback reason")
        if value["fallback_reason"] is not None:
            text(value["fallback_reason"], 500)
        array(value["required_labels"], 0, 40)
        for label in value["required_labels"]:
            text(label, empty=True)
        array(value["evidence_refs"], 0, 12)
        for ref in value["evidence_refs"]:
            identifier(ref)
        unique(value["evidence_refs"])
        layout_fields(value["strategy"], value["layout"])


class VisualPreview(_Contract):
    __slots__ = ()

    def _validate(self, value):
        fields(value, ("schema_version", "source_hash", "plan_hash", "renderer_version", "width", "height", "svg", "status", "warnings", "errors", "contains_text", "strategy"))
        require(value["schema_version"] == PREVIEW_VERSION, "version", "Unsupported preview schema")
        digest(value["source_hash"])
        digest(value["plan_hash"])
        require(value["renderer_version"] == RENDERER_VERSION, "version", "Unsupported renderer version")
        require(type(value["width"]) is int and value["width"] == 1920 and type(value["height"]) is int and value["height"] == 1080, "canvas", "Only 1920x1080 is supported")
        require(type(value["strategy"]) is str and value["strategy"] in STRATEGIES, "operation", "Unsupported strategy")
        require(value["status"] in ("valid", "invalid", "preserved"), "schema", "Invalid status")
        require(type(value["contains_text"]) is bool, "schema", "contains_text must be boolean")
        for key in ("warnings", "errors"):
            array(value[key], 0, 32)
            for entry in value[key]:
                text(entry, 1000)
        if value["status"] == "valid":
            require(type(value["svg"]) is str and len(value["svg"]) <= 50_000 and not value["errors"] and value["contains_text"] and value["strategy"] != "preserve_original", "preview", "Valid preview requires SVG text and no errors")
            # Lazy import keeps contracts independent of provider/application modules.
            # This checks the SVG dialect even when deserializing an external preview.
            from .validation import validate_svg
            checks = validate_svg(value["svg"])
            require(checks.valid, "unsafe_preview", "; ".join(checks.errors))
        else:
            require(value["svg"] is None and not value["contains_text"], "preview", "Failed/preserved results cannot expose SVG")
            require(bool(value["errors"]) == (value["status"] == "invalid"), "preview", "Invalid results require errors")
            require(value["status"] != "preserved" or value["strategy"] == "preserve_original", "preview", "Preserved result requires preserve_original")
