import json
import os
import sys
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

sys.modules.pop("avatar", None)
adapters = importlib.import_module("avatar.canonical_adapters")  # noqa: E402


def _has_cross_uid_write(path: Path) -> bool:
    if os.name == "nt":
        return os.access(path, os.W_OK)
    return bool(path.stat().st_mode & 0o002)


def test_musetalk_service_writable_dir_helper_scopes_to_generated_dir(tmp_path):
    work_dir = tmp_path / "preview.mp4.musetalk.service_chunks_preview-326-0"
    child_dir = work_dir / "chunk_tmp"

    adapters.ensure_writable_dir(work_dir)
    child_dir.mkdir()
    adapters.ensure_writable_dir(work_dir)

    assert work_dir.exists()
    assert child_dir.exists()
    assert _has_cross_uid_write(work_dir)
    assert _has_cross_uid_write(child_dir)


def test_musetalk_runner_reports_timeout(monkeypatch):
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "0")
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo mock")
    monkeypatch.setenv("AVATAR_STAGE_TIMEOUT_MUSETALK_SECONDS", "1")
    monkeypatch.setattr(adapters, "_check_gpu_headroom", lambda: None)

    def fake_run_command(*, stage_name, command, env_overrides=None, timeout_seconds=None):
        return False, f"{stage_name}_timeout", {
            "command": command,
            "return_code": None,
            "stderr": "timeout",
            "timeout_seconds": float(timeout_seconds or 0.0),
            "stage_name": stage_name,
        }

    monkeypatch.setattr(adapters, "_run_command", fake_run_command)

    result = adapters.run_musetalk(
        source_image="/tmp/source.png",
        source_video="",
        audio_path="/tmp/audio.wav",
        output_path="/tmp/out.mp4",
    )

    assert result.success is False
    assert result.engine == "musetalk"
    assert "musetalk_timeout" in (result.error or "")


def test_musetalk_runner_supports_preview_timeout_label(monkeypatch):
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "0")
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo mock")
    monkeypatch.setattr(adapters, "_check_gpu_headroom", lambda: None)

    def fake_run_command(*, stage_name, command, env_overrides=None, timeout_seconds=None):
        return False, f"{stage_name}_timeout", {
            "command": command,
            "return_code": None,
            "stderr": "timeout",
            "timeout_seconds": float(timeout_seconds or 0.0),
            "stage_name": stage_name,
        }

    monkeypatch.setattr(adapters, "_run_command", fake_run_command)

    result = adapters.run_musetalk(
        source_image="/tmp/source.png",
        source_video="/tmp/liveportrait.mp4",
        audio_path="/tmp/audio.wav",
        output_path="/tmp/out.mp4",
        timeout_seconds=7,
        stage_name="preview_musetalk",
    )

    assert result.success is False
    assert result.engine == "musetalk"
    assert result.error == "preview_musetalk_timeout"
    assert result.details.get("timeout_seconds") == 7
    assert result.details.get("stage_name") == "preview_musetalk"


def test_musetalk_runner_low_vram_adapts_instead_of_rejecting(monkeypatch):
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "0")
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo mock")
    monkeypatch.setenv("AVATAR_LOW_VRAM_MUSETALK_BATCH_SIZE", "2")
    monkeypatch.setenv("AVATAR_LOW_VRAM_MUSETALK_TIMEOUT_MULTIPLIER", "2.0")
    monkeypatch.setattr(adapters, "_check_gpu_headroom", lambda: "low_gpu_headroom:free_mib=900 total_mib=4096 required_mib=1800")

    captured = {}

    def fake_run_command(*, stage_name, command, env_overrides=None, timeout_seconds=None):
        captured["stage_name"] = stage_name
        captured["command"] = command
        captured["env_overrides"] = dict(env_overrides or {})
        captured["timeout_seconds"] = float(timeout_seconds or 0.0)
        return True, "", {"command": command, "return_code": 0, "stage_name": stage_name, "elapsed_seconds": 1.0}

    monkeypatch.setattr(adapters, "_run_command", fake_run_command)

    result = adapters.run_musetalk(
        source_image="/tmp/source.png",
        source_video="",
        audio_path="/tmp/audio.wav",
        output_path="/tmp/out.mp4",
        timeout_seconds=100,
        env_overrides={"MUSETALK_BATCH_SIZE": "8"},
    )

    assert result.success is True
    assert captured["stage_name"] == "musetalk"
    assert captured["env_overrides"].get("MUSETALK_BATCH_SIZE") == "2"
    assert captured["timeout_seconds"] >= 200


def test_preview_musetalk_uses_service_when_enabled_and_healthy(monkeypatch):
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo mock")
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_ROUTE", "subprocess")
    monkeypatch.setenv("AVATAR_PREVIEW_FORCE_ISOLATED_MUSETALK", "1")
    monkeypatch.setattr(adapters, "_check_gpu_headroom", lambda: None)

    service_calls = {"count": 0}
    subprocess_calls = {"count": 0}

    monkeypatch.setattr(adapters, "_musetalk_service_url", lambda: "http://127.0.0.1:17860")
    monkeypatch.setattr(
        adapters,
        "_musetalk_service_health",
        lambda _url: {"status": "ready", "ready_for_inference": True, "models_loaded": True},
    )

    def fake_service_call(*args, **kwargs):
        service_calls["count"] += 1
        return adapters.EngineResult(True, "musetalk", kwargs.get("output_path", ""), "", "", {
            "route": "service",
            "run_id": kwargs.get("run_id"),
        })

    def fake_run_command(*, stage_name, command, env_overrides=None, timeout_seconds=None):
        subprocess_calls["count"] += 1
        return True, "", {"command": command, "return_code": 0, "stage_name": stage_name, "elapsed_seconds": 0.5}

    monkeypatch.setattr(adapters, "_run_via_musetalk_service", fake_service_call)
    monkeypatch.setattr(adapters, "_run_command", fake_run_command)

    result = adapters.run_musetalk(
        source_image="/tmp/source.png",
        source_video="/tmp/liveportrait.mp4",
        audio_path="/tmp/audio.wav",
        output_path="/tmp/out.mp4",
        stage_name="preview_musetalk",
    )

    assert result.success is True
    assert subprocess_calls["count"] == 0
    assert service_calls["count"] == 1
    assert result.details.get("route") == "service"


def test_musetalk_service_payload_includes_runtime_parity_fields(monkeypatch):
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo mock")
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "1")
    monkeypatch.setattr(adapters, "_musetalk_service_url", lambda: "http://127.0.0.1:17860")
    monkeypatch.setattr(
        adapters,
        "_musetalk_service_health",
        lambda _url: {"status": "ready", "ready_for_inference": True, "models_loaded": True},
    )
    monkeypatch.setattr(adapters, "_probe_duration_seconds", lambda _path: 2.0)

    captured: dict[str, object] = {}

    def fake_service_call(*args, **kwargs):
        captured["params"] = dict(kwargs["params"])
        captured["timeout_seconds"] = kwargs["timeout_seconds"]
        captured["stage_budget_timeout_seconds"] = kwargs["stage_budget_timeout_seconds"]
        return adapters.EngineResult(True, "musetalk", kwargs.get("output_path", ""), "", "", {
            "route": "service",
            "run_id": kwargs.get("run_id"),
        })

    monkeypatch.setattr(adapters, "_run_via_musetalk_service", fake_service_call)

    result = adapters.run_musetalk(
        source_image="/tmp/source.png",
        source_video="/tmp/liveportrait.mp4",
        audio_path="/tmp/audio.wav",
        output_path="/tmp/out.mp4",
        timeout_seconds=123,
        stage_name="preview_musetalk",
        env_overrides={
            "MUSETALK_BATCH_SIZE": "2",
            "MUSETALK_FPS": "16",
            "MUSETALK_USE_FLOAT16": "1",
            "MUSETALK_LEFT_CHEEK_WIDTH": "72",
            "MUSETALK_RIGHT_CHEEK_WIDTH": "72",
            "MUSETALK_TARGET_FRAME_COUNT": "24",
            "MUSETALK_TARGET_DURATION_SECONDS": "1.500000",
            "MUSETALK_PREVIEW_FAST_MODE": "1",
            "MUSETALK_PREVIEW_MAX_WIDTH": "384",
            "MUSETALK_CHUNK_MAX_SECONDS": "15",
            "MUSETALK_CHUNK_TIMEOUT_SECONDS": "300",
            "MUSETALK_IDLE_TIMEOUT_SECONDS": "120",
        },
    )

    assert result.success is True
    params = captured["params"]
    assert params["batch_size"] == 2
    assert params["fps"] == 16
    assert params["use_float16"] is True
    assert params["version"] == "v15"
    assert params["left_cheek_width"] == 72
    assert params["right_cheek_width"] == 72
    assert params["target_frame_count"] == 24
    assert params["target_duration_seconds"] == 1.5
    assert params["preview_fast_mode"] is True
    assert params["preview_max_width"] == 384
    assert params["chunk_max_seconds"] == 15.0
    assert params["chunk_timeout_seconds"] == 300.0
    assert params["idle_timeout_seconds"] == 120.0
    assert params["estimated_chunk_count"] == 1
    assert params["stage_budget_timeout_seconds"] == captured["stage_budget_timeout_seconds"]
    assert params["http_timeout_seconds"] == captured["timeout_seconds"]


def test_musetalk_unhealthy_service_falls_back_only_when_enabled(monkeypatch):
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo mock")
    monkeypatch.setattr(adapters, "_check_gpu_headroom", lambda: None)
    monkeypatch.setattr(adapters, "_musetalk_service_url", lambda: "http://127.0.0.1:17860")
    monkeypatch.setattr(
        adapters,
        "_musetalk_service_health",
        lambda _url: {"status": "loading", "ready_for_inference": False, "models_loaded": False},
    )

    service_calls = {"count": 0}
    subprocess_calls = {"count": 0}

    def fake_service_call(*args, **kwargs):
        service_calls["count"] += 1
        return adapters.EngineResult(False, "musetalk", kwargs.get("output_path", ""), "unexpected")

    def fake_run_command(*, stage_name, command, env_overrides=None, timeout_seconds=None):
        subprocess_calls["count"] += 1
        return True, "", {"command": command, "return_code": 0, "stage_name": stage_name, "elapsed_seconds": 0.5}

    monkeypatch.setattr(adapters, "_run_via_musetalk_service", fake_service_call)
    monkeypatch.setattr(adapters, "_run_command", fake_run_command)

    result = adapters.run_musetalk(
        source_image="/tmp/source.png",
        source_video="/tmp/liveportrait.mp4",
        audio_path="/tmp/audio.wav",
        output_path="/tmp/out.mp4",
        stage_name="preview_musetalk",
    )

    assert result.success is True
    assert service_calls["count"] == 0
    assert subprocess_calls["count"] == 1
    assert result.details.get("route") == "standalone"
    assert result.details.get("route_reason") == "service_enabled_health_unready"


def test_musetalk_unhealthy_service_without_fallback_fails_clearly(monkeypatch):
    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "0")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo mock")
    monkeypatch.setattr(adapters, "_musetalk_service_url", lambda: "http://127.0.0.1:17860")
    monkeypatch.setattr(
        adapters,
        "_musetalk_service_health",
        lambda _url: {"status": "error", "ready_for_inference": False, "models_error": "boom"},
    )

    subprocess_calls = {"count": 0}

    def fake_run_command(*, stage_name, command, env_overrides=None, timeout_seconds=None):
        subprocess_calls["count"] += 1
        return True, "", {}

    monkeypatch.setattr(adapters, "_run_command", fake_run_command)

    result = adapters.run_musetalk(
        source_image="/tmp/source.png",
        source_video="",
        audio_path="/tmp/audio.wav",
        output_path="/tmp/out.mp4",
        stage_name="preview_musetalk",
    )

    assert result.success is False
    assert result.error == "musetalk_service_unavailable"
    assert subprocess_calls["count"] == 0
    assert result.details.get("route_reason") == "service_enabled_health_unready"


def test_musetalk_service_route_preserves_run_hashes_and_validation(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "out.mp4"
    source.write_bytes(b"source-bytes")
    audio.write_bytes(b"audio-bytes")
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def __init__(self, payload: dict):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        captured["payload"] = payload
        captured["timeout"] = timeout
        output.write_bytes(b"video-bytes")
        sidecar = adapters._musetalk_debug_sidecar_path(output)
        sidecar.write_text(
            json.dumps(
                {
                    "musetalk_run_id": payload["run"]["run_id"],
                    "input_reference_image_sha256": payload["run"]["source_image_sha256"],
                    "input_reference_video_sha256": "",
                    "input_audio_sha256": payload["run"]["audio_sha256"],
                }
            ),
            encoding="utf-8",
        )
        return FakeResponse(
            {
                "success": True,
                "output_path": str(output),
                "elapsed_seconds": 1.25,
                "cold_start_seconds": 0.0,
                "model_load_seconds": 0.0,
                "inference_seconds": 1.25,
                "stage_timings": {
                    "model_load_seconds": 0.0,
                    "face_landmark_extraction_seconds": 0.4,
                    "inference_loop_seconds": 0.2,
                },
            }
        )

    monkeypatch.setattr(adapters.urllib.request, "urlopen", fake_urlopen)

    result = adapters._run_via_musetalk_service(
        "http://127.0.0.1:17860",
        source_image=str(source),
        source_video="",
        audio_path=str(audio),
        output_path=str(output),
        params={"batch_size": 8},
        timeout_seconds=500.0,
        stage_budget_timeout_seconds=440.0,
        stage_name="preview_musetalk",
        run_id="run-123",
        route_reason="service_enabled_health_ready",
        service_health={"status": "ready", "ready_for_inference": True},
    )

    assert result.success is True
    payload = captured["payload"]
    assert payload["run"]["run_id"] == "run-123"
    assert payload["run"]["source_image_sha256"] == adapters._sha256_file(source)
    assert payload["run"]["audio_sha256"] == adapters._sha256_file(audio)
    assert result.details["model_load_seconds"] == 0.0
    assert result.details["face_landmark_seconds"] == 0.4
    assert result.details["inference_loop_seconds"] == 0.2
    assert result.details["svc_timeout_seconds"] == 500.0


def test_musetalk_service_chunked_route_stitches_long_lesson_segment(monkeypatch, tmp_path):
    source_image = tmp_path / "source.png"
    source_video = tmp_path / "liveportrait.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "avatar.mp4"
    source_image.write_bytes(b"source-image")
    source_video.write_bytes(b"source-video")
    audio.write_bytes(b"audio")
    chunk_calls: list[dict] = []
    chunk_output_dirs: list[Path] = []

    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "0")
    monkeypatch.setenv("MUSETALK_CHUNK_MAX_SECONDS", "15")
    monkeypatch.setattr(adapters, "_musetalk_service_url", lambda: "http://127.0.0.1:17860")
    monkeypatch.setattr(
        adapters,
        "_musetalk_service_health",
        lambda _url: {"status": "ready", "ready_for_inference": True},
    )
    monkeypatch.setattr(adapters, "_probe_duration_seconds", lambda path: 30.264 if str(path) in {str(source_video), str(audio)} else 10.0)
    monkeypatch.setattr(adapters, "_probe_frame_count", lambda _path: 150)

    def fake_prepare_chunk(**kwargs):
        chunk_audio = tmp_path / f"chunk_{kwargs['chunk_index']:04d}.wav"
        chunk_video = tmp_path / f"chunk_{kwargs['chunk_index']:04d}.mp4"
        chunk_audio.write_bytes(b"chunk-audio")
        chunk_video.write_bytes(b"chunk-video")
        return str(chunk_video), str(chunk_audio)

    def fake_service(*_args, **kwargs):
        out = Path(kwargs["output_path"])
        chunk_output_dirs.append(out.parent)
        assert ".service_chunks_" in out.parent.name
        assert _has_cross_uid_write(out.parent)
        out.write_bytes(b"chunk-output")
        adapters._musetalk_debug_sidecar_path(out).write_text(
            json.dumps(
                {
                    "musetalk_run_id": kwargs["run_id"],
                    "input_reference_image_sha256": adapters._sha256_file(source_image),
                    "input_reference_video_sha256": adapters._sha256_file(Path(kwargs["source_video"])),
                    "input_audio_sha256": adapters._sha256_file(Path(kwargs["audio_path"])),
                }
            ),
            encoding="utf-8",
        )
        chunk_calls.append(kwargs)
        return adapters.EngineResult(True, "musetalk", str(out), "", "", {"elapsed_seconds": 1.5, "route": "service"})

    def fake_concat(*, chunk_outputs, output_path, work_dir):
        assert len(chunk_outputs) == 3
        output_path.write_bytes(b"stitched-output")

    monkeypatch.setattr(adapters, "_prepare_service_chunk_media", fake_prepare_chunk)
    monkeypatch.setattr(adapters, "_run_via_musetalk_service", fake_service)
    monkeypatch.setattr(adapters, "_concat_service_chunk_outputs", fake_concat)

    result = adapters.run_musetalk(
        source_image=str(source_image),
        source_video=str(source_video),
        audio_path=str(audio),
        output_path=str(output),
        stage_name="musetalk",
        timeout_seconds=900.0,
    )

    assert result.success is True
    assert result.error != "musetalk_service_chunking_required"
    assert result.details["route"] == "service_chunked"
    assert result.details["chunk_count"] == 3
    assert len(chunk_calls) == 3
    assert chunk_output_dirs
    assert all(_has_cross_uid_write(path) for path in chunk_output_dirs)
    assert output.exists()
    debug = json.loads(adapters._musetalk_debug_sidecar_path(output).read_text(encoding="utf-8"))
    assert debug["route"] == "service_chunked"
    assert debug["chunk_count"] == 3
    assert debug["final_stitched_output_path"] == str(output)


def test_musetalk_service_chunked_does_not_require_standalone_fallback(monkeypatch, tmp_path):
    source_image = tmp_path / "source.png"
    source_video = tmp_path / "liveportrait.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "avatar.mp4"
    for path in [source_image, source_video, audio]:
        path.write_bytes(path.name.encode("utf-8"))

    monkeypatch.setenv("AVATAR_MUSETALK_SERVICE_ENABLED", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "0")
    monkeypatch.setenv("MUSETALK_CHUNK_MAX_SECONDS", "15")
    monkeypatch.setattr(adapters, "_musetalk_service_url", lambda: "http://127.0.0.1:17860")
    monkeypatch.setattr(adapters, "_musetalk_service_health", lambda _url: {"status": "ready", "ready_for_inference": True})
    monkeypatch.setattr(adapters, "_probe_duration_seconds", lambda path: 30.264 if str(path) in {str(source_video), str(audio)} else 10.0)
    monkeypatch.setattr(
        adapters,
        "_run_via_musetalk_service_chunked",
        lambda *_args, **kwargs: adapters.EngineResult(
            True,
            "musetalk",
            kwargs["output_path"],
            "",
            "",
            {"route": "service_chunked", "chunk_count": kwargs["chunk_count"]},
        ),
    )

    result = adapters.run_musetalk(
        source_image=str(source_image),
        source_video=str(source_video),
        audio_path=str(audio),
        output_path=str(output),
        stage_name="musetalk",
    )

    assert result.success is True
    assert result.details["route"] == "service_chunked"
    assert result.error != "musetalk_service_chunking_required"
