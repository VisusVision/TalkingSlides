import hashlib
import json
from pathlib import Path


EVIDENCE_ROOT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "avatar-run-011"


def _read_json(name: str) -> dict:
    return json.loads((EVIDENCE_ROOT / name).read_text(encoding="utf-8"))


def _all_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)
    elif isinstance(value, str):
        yield value


def test_run011_evidence_is_hash_bound_and_privacy_safe():
    manifest = _read_json("evidence-manifest.json")
    report_path = EVIDENCE_ROOT / "avatar-evaluation.json"
    report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()

    assert report_hash == manifest["artifacts"]["evaluation_report"]["sha256"]
    assert manifest["artifacts"]["comparison_video"]["committed"] is False
    assert manifest["privacy"] == {
        "real_person_media_committed": False,
        "absolute_path_leak_scan_passed": True,
    }
    forbidden = ("/app/", "storage_local", "c:\\users\\")
    assert not [
        value
        for value in _all_strings(_read_json("avatar-evaluation.json"))
        if any(marker in value.lower() for marker in forbidden)
    ]


def test_run011_evidence_requires_all_model_backed_gates():
    report = _read_json("avatar-evaluation.json")

    assert report["version"] == "avatar-evaluation-v2"
    assert report["recommendation"] == "prosody"
    assert report["fair_comparison"] is True
    assert report["warnings"] == []
    assert report["automated_claims"]["model_verified_identity_and_lip_sync"] is True
    assert [variant["kind"] for variant in report["variants"]] == [
        "generic",
        "personal",
        "prosody",
    ]
    for variant in report["variants"]:
        assert variant["eligible"] is True
        assert variant["identity"]["provider"] == "opencv_yunet_sface"
        assert variant["identity"]["assurance"] == "strong"
        assert variant["identity"]["passed"] is True
        assert variant["lip_sync"]["provider"] == "latentsync_syncnet"
        assert variant["lip_sync"]["assurance"] == "strong"
        assert variant["lip_sync"]["passed"] is True
        assert abs(variant["lip_sync"]["evidence"]["av_offset_milliseconds"]) <= 80.0
