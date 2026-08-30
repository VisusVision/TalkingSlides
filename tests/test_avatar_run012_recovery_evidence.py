import hashlib
import json
from pathlib import Path


EVIDENCE_ROOT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "avatar-run-012"


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


def test_run012_recovery_evidence_is_hash_bound_and_privacy_safe():
    manifest = _read_json("evidence-manifest.json")
    evidence_path = EVIDENCE_ROOT / "recovery-evidence.json"

    assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == (
        manifest["artifacts"]["recovery_evidence"]["sha256"]
    )
    assert manifest["artifacts"]["recovered_video"]["committed"] is False
    assert manifest["privacy"] == {
        "real_person_media_committed": False,
        "absolute_path_leak_scan_passed": True,
    }
    forbidden = ("/app/", "storage_local", "c:\\users\\")
    for filename in ("recovery-evidence.json", "evidence-manifest.json"):
        assert not [
            value
            for value in _all_strings(_read_json(filename))
            if any(marker in value.lower() for marker in forbidden)
        ]


def test_run012_proves_one_bounded_retry_and_handoff_reuse():
    evidence = _read_json("recovery-evidence.json")
    recovery = evidence["recovery"]
    fault = evidence["fault_contract"]
    handoff = evidence["liveportrait_handoff"]

    assert evidence["version"] == "avatar-musetalk-recovery-evidence-v1"
    assert fault["injected_fault"] == "first_infer_http_503"
    assert fault["infer_requests"] == 2
    assert fault["injected_failures"] == 1
    assert recovery["success"] is True
    assert recovery["retry_limit"] == 1
    assert recovery["retry_count"] == 1
    assert recovery["failure_classification"] == "transient_service_transport:service_http_503"
    assert recovery["recovery_succeeded"] is True
    assert recovery["retry_exhausted"] is False
    assert [attempt["success"] for attempt in recovery["attempts"]] == [False, True]
    assert handoff["sha256_before"] == handoff["sha256_after"]
    assert handoff["unchanged"] is True
    assert handoff["liveportrait_rerun"] is False
    assert evidence["negative_control"]["non_transient_permission_failure_retried"] is False


def test_run012_recovered_output_passes_strong_quality_gates():
    quality = _read_json("recovery-evidence.json")["quality"]

    assert quality["decision"] == "passed"
    assert quality["publish_allowed"] is True
    assert quality["strict_validation_passed"] is True
    assert quality["artifact_detected"] is False
    assert quality["identity"]["provider"] == "opencv_yunet_sface"
    assert quality["identity"]["assurance"] == "strong"
    assert quality["identity"]["passed"] is True
    assert quality["identity"]["model_hashes_verified"] is True
    assert quality["lip_sync"]["provider"] == "latentsync_syncnet"
    assert quality["lip_sync"]["assurance"] == "strong"
    assert quality["lip_sync"]["passed"] is True
    assert quality["lip_sync"]["model_hash_verified"] is True
    assert abs(quality["lip_sync"]["av_offset_milliseconds"]) <= 80.0
    assert quality["temporal"]["passed"] is True
