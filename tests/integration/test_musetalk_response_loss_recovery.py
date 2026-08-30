from __future__ import annotations

import threading
from pathlib import Path

import pytest

from services.avatar import canonical_adapters
from services.avatar.canonical_pipeline import _run_musetalk_with_transient_recovery
from services.scripts import musetalk_service as service
from services.scripts.musetalk_recovery_proxy import RecoveryFaultState, create_server
from services.scripts.run_musetalk_recovery_smoke import _response_loss_idempotency_evidence


def test_response_loss_evidence_contract_requires_replay_and_stable_output_hash() -> None:
    fault_state = {
        "version": "musetalk-recovery-fault-v2",
        "fault_mode": "post_infer_disconnect",
        "infer_requests": 2,
        "forwarded_infer_requests": 2,
        "upstream_responses": 2,
        "injected_failures": 1,
        "dropped_responses": 1,
        "response_audit": [
            {
                "status": 200,
                "dropped": True,
                "run_id": "run-1",
                "success": True,
                "idempotency_status": "owner",
                "idempotency_replayed": False,
                "output": {"present": True, "sha256": "video-hash"},
            },
            {
                "status": 200,
                "dropped": False,
                "run_id": "run-1",
                "success": True,
                "idempotency_status": "completed_replay",
                "idempotency_replayed": True,
                "output": {"present": True, "sha256": "video-hash"},
            },
        ],
    }

    proved = _response_loss_idempotency_evidence(
        fault_state=fault_state,
        output_hash="video-hash",
    )
    changed_output = _response_loss_idempotency_evidence(
        fault_state=fault_state,
        output_hash="different-hash",
    )

    assert proved["proved"] is True
    assert proved["idempotency_statuses"] == ["owner", "completed_replay"]
    assert proved["output_sha256_observations"] == ["video-hash", "video-hash"]
    assert changed_output["proved"] is False


@pytest.mark.integration
def test_response_lost_after_inference_retries_as_completed_replay_without_second_gpu_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service._idempotency.clear()
    models_were_loaded = service._models_loaded.is_set()
    service._models_loaded.set()
    monkeypatch.setattr(service, "_models_error", None)
    monkeypatch.setattr(
        service,
        "_preflight_disk_capacity",
        lambda **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(canonical_adapters, "_probe_duration_seconds", lambda _path: 0.0)

    source = tmp_path / "source.png"
    audio = tmp_path / "voice.wav"
    output = tmp_path / "avatar.mp4"
    source.write_bytes(b"source-image")
    audio.write_bytes(b"source-audio")
    inference_calls = {"count": 0}

    def _infer(
        *,
        source_image: str,
        source_video: str,
        audio_path: str,
        output_path: str,
        params: dict,
        run: dict,
    ) -> dict:
        inference_calls["count"] += 1
        output_file = Path(output_path)
        output_file.write_bytes(b"stable-generated-video")
        service._write_debug_sidecar(
            output_path=output_file,
            source_image=source_image,
            source_video=source_video,
            selected_source=source_image,
            audio_path=audio_path,
            params=params,
            run=run,
            stage_timings={"inference_loop_seconds": 0.01},
            elapsed_seconds=0.02,
        )
        return {
            "success": True,
            "output_path": output_path,
            "elapsed_seconds": 0.02,
            "stage_timings": {"inference_loop_seconds": 0.01},
        }

    monkeypatch.setattr(service, "_infer", _infer)
    upstream = service._ThreadedHTTPServer(("127.0.0.1", 0), service._Handler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    state_path = tmp_path / "response-loss-state.json"
    state = RecoveryFaultState(
        fail_infer_count=1,
        state_path=state_path,
        fault_mode=RecoveryFaultState.POST_INFER_DISCONNECT,
    )
    proxy = create_server(listen_port=0, upstream_port=upstream.server_port, state=state)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_PORT", str(proxy.server_port))
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "0")
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_HTTP_TIMEOUT_MARGIN_SECONDS", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_TRANSIENT_RETRY_COUNT", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_TRANSIENT_RETRY_DELAY_SECONDS", "0")

    try:
        result = _run_musetalk_with_transient_recovery(
            source_image=str(source),
            source_video="",
            audio_path=str(audio),
            output_path=str(output),
            env_overrides={"AVATAR_MUSETALK_RUN_ID": "response-lost-run"},
            timeout_seconds=1.0,
            stage_name="response_loss_smoke",
        )
    finally:
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()
        proxy_thread.join(timeout=2.0)
        upstream_thread.join(timeout=2.0)
        service._idempotency.clear()
        if not models_were_loaded:
            service._models_loaded.clear()

    audit = state.payload()
    attempts = list(result.details.get("transient_retry_attempts") or [])
    response_audit = audit["response_audit"]

    assert result.success is True
    assert output.read_bytes() == b"stable-generated-video"
    assert inference_calls["count"] == 1
    assert result.details["transient_retry_count"] == 1
    assert result.details["transient_recovery_succeeded"] is True
    assert result.details["idempotency_run_id"] == "response-lost-run"
    assert [attempt["success"] for attempt in attempts] == [False, True]
    assert attempts[0]["classification"] == "transient_service_transport:remote_end_closed_connection"
    assert attempts[1]["idempotency_status"] == "completed_replay"
    assert attempts[1]["idempotency_replayed"] is True

    assert audit["fault_mode"] == "post_infer_disconnect"
    assert audit["infer_requests"] == 2
    assert audit["forwarded_infer_requests"] == 2
    assert audit["injected_failures"] == 1
    assert audit["dropped_responses"] == 1
    assert [item["idempotency_status"] for item in response_audit] == [
        "owner",
        "completed_replay",
    ]
    assert [item["idempotency_replayed"] for item in response_audit] == [False, True]
    assert [item["request_index"] for item in response_audit] == [1, 2]
    assert [item["run_id"] for item in response_audit] == [
        "response-lost-run",
        "response-lost-run",
    ]
    assert len({item["output"]["sha256"] for item in response_audit}) == 1
    assert all(item["output"]["present"] for item in response_audit)
