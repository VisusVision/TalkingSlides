import hashlib
import json
from pathlib import Path


EVIDENCE_ROOT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "avatar-run-013"


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


def test_run013_evidence_is_hash_bound_and_privacy_safe():
    manifest = _read_json("evidence-manifest.json")
    evidence_path = EVIDENCE_ROOT / "response-loss-evidence.json"

    assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == (
        manifest["artifacts"]["response_loss_evidence"]["sha256"]
    )
    assert manifest["artifacts"]["generated_video"]["committed"] is False
    assert manifest["privacy"] == {
        "real_person_media_committed": False,
        "raw_logs_committed": False,
        "absolute_path_leak_scan_passed": True,
    }
    forbidden = ("/app/", "storage_local", "c:\\users\\")
    for filename in ("response-loss-evidence.json", "evidence-manifest.json"):
        assert not [
            value
            for value in _all_strings(_read_json(filename))
            if any(marker in value.lower() for marker in forbidden)
        ]


def test_run013_proves_one_real_inference_then_completed_replay():
    evidence = _read_json("response-loss-evidence.json")
    fault = evidence["fault_contract"]
    recovery = evidence["recovery"]

    assert evidence["version"] == "avatar-musetalk-response-loss-evidence-v1"
    assert evidence["runtime"]["real_gpu_inference"] is True
    assert evidence["runtime"]["cuda_available"] is True
    assert evidence["runtime"]["cuda_provider_verified"] is True
    assert fault["injected_fault"] == "post_infer_disconnect"
    assert fault["infer_requests"] == 2
    assert fault["forwarded_infer_requests"] == 2
    assert fault["dropped_responses"] == 1
    assert fault["service_request_start_count"] == 1
    assert fault["service_inference_complete_count"] == 1
    assert fault["real_inference_executions"] == 1
    assert [item["idempotency_status"] for item in fault["response_audit"]] == [
        "owner",
        "completed_replay",
    ]
    assert [item["idempotency_replayed"] for item in fault["response_audit"]] == [
        False,
        True,
    ]
    assert recovery["success"] is True
    assert recovery["retry_count"] == 1
    assert recovery["recovery_succeeded"] is True
    assert recovery["retry_exhausted"] is False
    assert recovery["final_idempotency_status"] == "completed_replay"
    assert recovery["failure_classification"] == (
        "transient_service_transport:remote_end_closed_connection"
    )


def test_run013_proves_handoff_and_output_bytes_are_stable():
    evidence = _read_json("response-loss-evidence.json")
    integrity = evidence["data_integrity"]
    output = evidence["output"]

    assert integrity["liveportrait_handoff_sha256_before"] == (
        integrity["liveportrait_handoff_sha256_after"]
    )
    assert integrity["liveportrait_handoff_unchanged"] is True
    assert len(integrity["output_sha256_observations"]) == 2
    assert len(set(integrity["output_sha256_observations"])) == 1
    assert integrity["output_sha256_observations"][0] == output["sha256"]
    assert integrity["output_unchanged_across_replay"] is True
    assert output["committed"] is False
    assert output["size_bytes"] == 1262247
    assert output["duration_seconds"] == 15.0
    assert output["width"] == 1024
    assert output["height"] == 1024
    assert output["fps"] == 25
    assert evidence["quality_scope"]["identity_and_lip_sync_reassessed"] is False
