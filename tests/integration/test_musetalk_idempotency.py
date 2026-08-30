from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from services.scripts import musetalk_service as service
from services.scripts.musetalk_idempotency import IdempotencyCoordinator


@pytest.mark.integration
def test_idempotency_join_timeout_stays_inside_http_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSETALK_IDEMPOTENCY_WAIT_TIMEOUT_SECONDS", "0")
    assert service._idempotency_wait_timeout_seconds({"http_timeout_seconds": 30}) == 25.0
    assert service._idempotency_wait_timeout_seconds({"http_timeout_seconds": 5}) == 1.0
    assert service._idempotency_wait_timeout_seconds({}) == 7200.0

    monkeypatch.setenv("MUSETALK_IDEMPOTENCY_WAIT_TIMEOUT_SECONDS", "10")
    assert service._idempotency_wait_timeout_seconds({"http_timeout_seconds": 30}) == 10.0
    assert service._idempotency_wait_timeout_seconds({"http_timeout_seconds": 8}) == 3.0


@pytest.mark.integration
def test_idempotency_coordinator_joins_replays_and_rejects_conflicts() -> None:
    coordinator = IdempotencyCoordinator()
    owner = coordinator.reserve(
        run_id="run-1",
        fingerprint="fingerprint-a",
        output_path="/tmp/avatar.mp4",
        ttl_seconds=3600,
        max_entries=8,
    )
    follower = coordinator.reserve(
        run_id="run-1",
        fingerprint="fingerprint-a",
        output_path="/tmp/avatar.mp4",
        ttl_seconds=3600,
        max_entries=8,
    )

    assert owner.action == "owner"
    assert owner.entry is not None
    assert follower.action == "follower"
    assert follower.entry is owner.entry

    coordinator.complete(
        owner.entry,
        status_code=200,
        payload={"success": True, "output_path": "/tmp/avatar.mp4"},
        retain=True,
    )

    assert coordinator.wait(follower.entry, timeout_seconds=0.1) == (
        200,
        {"success": True, "output_path": "/tmp/avatar.mp4"},
    )
    assert coordinator.reserve(
        run_id="run-1",
        fingerprint="fingerprint-a",
        output_path="/tmp/avatar.mp4",
        ttl_seconds=3600,
        max_entries=8,
    ).action == "replay"
    assert coordinator.reserve(
        run_id="run-1",
        fingerprint="fingerprint-b",
        output_path="/tmp/avatar.mp4",
        ttl_seconds=3600,
        max_entries=8,
    ).action == "conflict"

    failed_owner = coordinator.reserve(
        run_id="failed-run",
        fingerprint="failed-fingerprint",
        output_path="/tmp/failed.mp4",
        ttl_seconds=3600,
        max_entries=8,
    )
    assert failed_owner.entry is not None
    coordinator.complete(
        failed_owner.entry,
        status_code=500,
        payload={"success": False, "error": "inference_failed"},
        retain=False,
    )
    assert coordinator.reserve(
        run_id="failed-run",
        fingerprint="failed-fingerprint",
        output_path="/tmp/failed.mp4",
        ttl_seconds=3600,
        max_entries=8,
    ).action == "owner"


@pytest.mark.integration
def test_musetalk_http_duplicate_joins_owner_then_replays_without_second_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service._idempotency.clear()
    was_loaded = service._models_loaded.is_set()
    service._models_loaded.set()
    monkeypatch.setattr(service, "_models_error", None)
    source = tmp_path / "source.png"
    audio = tmp_path / "voice.wav"
    output = tmp_path / "avatar.mp4"
    source.write_bytes(b"image")
    audio.write_bytes(b"audio")
    infer_started = threading.Event()
    follower_waiting = threading.Event()
    allow_infer_to_finish = threading.Event()
    inference_calls = {"count": 0}

    monkeypatch.setattr(
        service,
        "_preflight_request_inputs",
        lambda **_kwargs: {"source": source, "audio": audio},
    )
    monkeypatch.setattr(service, "_preflight_output_path", lambda _path: output)
    monkeypatch.setattr(service, "_preflight_disk_capacity", lambda **_kwargs: {"ok": True})
    original_wait = service._idempotency.wait

    def _tracked_wait(entry, *, timeout_seconds):
        follower_waiting.set()
        return original_wait(entry, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(service._idempotency, "wait", _tracked_wait)

    def _infer(**_kwargs):
        inference_calls["count"] += 1
        infer_started.set()
        assert allow_infer_to_finish.wait(timeout=2.0)
        output.write_bytes(b"video")
        service._debug_sidecar_path(output).write_text(
            json.dumps({
                "musetalk_run_id": "run-duplicate",
                "input_reference_image_path": str(source),
                "input_reference_video_path": "",
                "input_audio_path": str(audio),
                "input_reference_image_sha256": "image-hash",
                "input_reference_video_sha256": "",
                "input_source_sha256": "image-hash",
                "input_audio_sha256": "audio-hash",
                "selected_musetalk_params": {"http_timeout_seconds": 30.0},
                "output_path": str(output),
            }),
            encoding="utf-8",
        )
        return {
            "success": True,
            "output_path": str(output),
            "elapsed_seconds": 0.1,
            "stage_timings": {},
        }

    monkeypatch.setattr(service, "_infer", _infer)
    server = service._ThreadedHTTPServer(("127.0.0.1", 0), service._Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    request_payload = {
        "source_image": str(source),
        "source_video": "",
        "audio_path": str(audio),
        "output_path": str(output),
        "params": {"http_timeout_seconds": 30.0},
        "run": {
            "run_id": "run-duplicate",
            "source_image_sha256": "image-hash",
            "source_sha256": "image-hash",
            "audio_sha256": "audio-hash",
        },
    }
    url = f"http://127.0.0.1:{server.server_port}/infer"
    responses: list[dict] = []

    def _post() -> None:
        request = urllib.request.Request(
            url,
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        responses.append(json.loads(urllib.request.urlopen(request, timeout=3.0).read()))

    owner_thread = threading.Thread(target=_post)
    follower_thread = threading.Thread(target=_post)
    try:
        owner_thread.start()
        assert infer_started.wait(timeout=2.0)
        follower_thread.start()
        assert follower_waiting.wait(timeout=2.0)
        allow_infer_to_finish.set()
        owner_thread.join(timeout=3.0)
        follower_thread.join(timeout=3.0)

        replay_request = urllib.request.Request(
            url,
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        replay = json.loads(urllib.request.urlopen(replay_request, timeout=2.0).read())

        conflict_payload = dict(request_payload)
        conflict_payload["params"] = {"http_timeout_seconds": 30.0, "batch_size": 99}
        conflict_request = urllib.request.Request(
            url,
            data=json.dumps(conflict_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as conflict_exc:
            urllib.request.urlopen(conflict_request, timeout=2.0)
        conflict = json.loads(conflict_exc.value.read())
    finally:
        allow_infer_to_finish.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)
        service._idempotency.clear()
        if not was_loaded:
            service._models_loaded.clear()

    assert not owner_thread.is_alive()
    assert not follower_thread.is_alive()
    assert inference_calls["count"] == 1
    assert len(responses) == 2
    assert {response["idempotency_status"] for response in responses} == {
        "owner",
        "joined_inflight",
    }
    assert replay["idempotency_status"] == "completed_replay"
    assert replay["idempotency_replayed"] is True
    assert inference_calls["count"] == 1
    assert conflict_exc.value.code == 409
    assert conflict["failure_category"] == "idempotency_conflict"
    assert conflict["retryable"] is False


@pytest.mark.integration
def test_persisted_idempotent_result_validates_hashes_and_params(tmp_path: Path) -> None:
    output = tmp_path / "avatar.mp4"
    output.write_bytes(b"video")
    params = {"batch_size": 2, "fps": 25}
    run = {
        "source_image_sha256": "image-hash",
        "source_video_sha256": "",
        "source_sha256": "image-hash",
        "audio_sha256": "audio-hash",
    }
    service._debug_sidecar_path(output).write_text(
        json.dumps({
            "musetalk_run_id": "persisted-run",
            "input_reference_image_path": str(tmp_path / "source.png"),
            "input_reference_video_path": "",
            "input_audio_path": str(tmp_path / "voice.wav"),
            "input_reference_image_sha256": "image-hash",
            "input_reference_video_sha256": "",
            "input_source_sha256": "image-hash",
            "input_audio_sha256": "audio-hash",
            "selected_musetalk_params": params,
            "output_path": str(output),
            "elapsed_seconds": 12.5,
            "stage_timings": {"inference_loop_seconds": 4.0},
        }),
        encoding="utf-8",
    )

    replay = service._persisted_idempotent_result(
        run_id="persisted-run",
        source_image=str(tmp_path / "source.png"),
        source_video="",
        audio_path=str(tmp_path / "voice.wav"),
        output_path=str(output),
        params=params,
        run=run,
    )

    assert replay is not None
    assert replay["success"] is True
    assert replay["elapsed_seconds"] == 12.5
    with pytest.raises(service.MuseTalkIdempotencyConflict):
        service._persisted_idempotent_result(
            run_id="persisted-run",
            source_image=str(tmp_path / "source.png"),
            source_video="",
            audio_path=str(tmp_path / "voice.wav"),
            output_path=str(output),
            params={"batch_size": 8, "fps": 25},
            run=run,
        )
