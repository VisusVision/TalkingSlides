from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

from services.scripts import musetalk_entrypoint as entrypoint
from services.scripts import musetalk_service as service


_SAMPLE_INFERENCE_TEXT = """
            # Set bbox_shift based on version
            if args.version == "v15":
                bbox_shift = 0  # v15 uses fixed bbox_shift
            else:
                bbox_shift = inference_config[task_id].get("bbox_shift", args.bbox_shift)  # v1 uses config or default

            # Extract frames from source video
            if get_file_type(video_path) == "video":
                save_dir_full = os.path.join(temp_dir, input_basename)
                os.makedirs(save_dir_full, exist_ok=True)

            cmd_img2video = f"ffmpeg -y -v warning -r {fps} -f image2 -i {result_img_save_path}/%08d.png -vcodec libx264 -vf format=yuv420p -crf 18 {temp_vid_path}"
            print("Video generation command:", cmd_img2video)
            os.system(cmd_img2video)

            cmd_combine_audio = f"ffmpeg -y -v warning -i {audio_path} -i {temp_vid_path} {output_vid_name}"
            print("Audio combination command:", cmd_combine_audio)
            os.system(cmd_combine_audio)

            # Clean up temporary files
            shutil.rmtree(result_img_save_path)
            os.remove(temp_vid_path)

            shutil.rmtree(save_dir_full)
            if not args.saved_coord:
                os.remove(crop_coord_save_path)

        except Exception as e:
            print("Error occurred during processing:", e)
"""


def _patch_sample(tmp_path: Path) -> str:
    src = tmp_path / "inference.py"
    dst = tmp_path / "inference.patched.py"
    src.write_text(_SAMPLE_INFERENCE_TEXT, encoding="utf-8")
    entrypoint._prepare_patched_inference_script(src, dst)
    return dst.read_text(encoding="utf-8")


@pytest.mark.integration
def test_prepare_patched_inference_initializes_save_dir_full(tmp_path: Path) -> None:
    patched = _patch_sample(tmp_path)

    assert 'save_dir_full = os.path.join(temp_dir, input_basename)' in patched
    assert 'if get_file_type(video_path) == "video":' in patched
    assert 'if save_dir_full and os.path.isdir(save_dir_full):' in patched
    assert 'shutil.rmtree(save_dir_full)' not in patched


@pytest.mark.integration
def test_prepare_patched_inference_raises_clear_mux_errors(tmp_path: Path) -> None:
    patched = _patch_sample(tmp_path)

    assert "mux_encode_stage=image_to_video_failed" in patched
    assert "mux_encode_stage=audio_mux_failed" in patched
    assert 'except Exception as e:\n            print("Error occurred during processing:", e)\n            raise\n' in patched


@pytest.mark.integration
def test_detect_inference_stage_markers() -> None:
    stage = "model_load"
    stage = entrypoint._detect_inference_stage("Extracting landmarks... time-consuming operation", stage)
    assert stage == "face_landmark_extraction"
    stage = entrypoint._detect_inference_stage("Starting inference", stage)
    assert stage == "inference_loop"
    stage = entrypoint._detect_inference_stage("Video generation command: ffmpeg ...", stage)
    assert stage == "mux_encode"
    stage = entrypoint._detect_inference_stage("Audio combination command: ffmpeg ...", stage)
    assert stage == "final_save"


@pytest.mark.integration
def test_stage_idle_timeout_map_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSETALK_STAGE_TIMEOUT_MODEL_LOAD_SECONDS", "111")
    monkeypatch.setenv("MUSETALK_STAGE_TIMEOUT_INFERENCE_LOOP_SECONDS", "222")
    timeouts = entrypoint._stage_idle_timeout_map()

    assert float(timeouts["model_load"]) == 111.0
    assert float(timeouts["inference_loop"]) == 222.0


@pytest.mark.integration
def test_musetalk_service_request_stage_timeout_uses_request_budget() -> None:
    timeout_floor = service._request_stage_timeout_floor(
        {
            "stage_budget_timeout_seconds": 7200.0,
            "chunk_timeout_seconds": 3600.0,
            "idle_timeout_seconds": 1200.0,
        }
    )

    assert timeout_floor == 7200.0
    assert max(service._stage_timeout_seconds("face_landmark_extraction", 900.0), timeout_floor) == 7200.0


@pytest.mark.integration
def test_musetalk_service_ffmpeg_failure_reports_stage_and_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(command, stdout=None, stderr=None, text=True, check=False, timeout=None):
        return subprocess.CompletedProcess(command, 17, "", "mux failed")

    monkeypatch.setattr(service.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        service._run_ffmpeg_stage(
            stage_name="audio_mux_encode",
            command=["ffmpeg", "-i", "input.wav", "-i", "temp.mp4", "out.mp4"],
        )

    message = str(excinfo.value)
    assert "musetalk_stage=audio_mux_encode" in message
    assert "command=ffmpeg -i input.wav -i temp.mp4 out.mp4" in message


@pytest.mark.integration
def test_musetalk_service_ffmpeg_missing_output_reports_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _fake_run(command, stdout=None, stderr=None, text=True, check=False, timeout=None):
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service.subprocess, "run", _fake_run)

    missing_output = tmp_path / "missing.mp4"
    with pytest.raises(RuntimeError) as excinfo:
        service._run_ffmpeg_stage(
            stage_name="image_to_video_encode",
            command=["ffmpeg", "-i", "frames/%08d.png", "temp.mp4"],
            expected_output=missing_output,
        )

    message = str(excinfo.value)
    assert "musetalk_stage=image_to_video_encode" in message
    assert f"output_path={missing_output}" in message


@pytest.mark.integration
def test_musetalk_service_ffmpeg_timeout_reports_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(command, stdout=None, stderr=None, text=True, check=False, timeout=None):
        raise subprocess.TimeoutExpired(command, float(timeout or 1.0))

    monkeypatch.setattr(service.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        service._run_ffmpeg_stage(
            stage_name="audio_mux_encode",
            command=["ffmpeg", "-i", "a.wav", "-i", "v.mp4", "out.mp4"],
            timeout_seconds=5.0,
        )

    message = str(excinfo.value)
    assert "musetalk_stage_timeout stage=audio_mux_encode" in message
    assert "ffmpeg -i a.wav -i v.mp4 out.mp4" in message


@pytest.mark.integration
def test_entrypoint_provider_check_requires_cuda_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeOrt:
        @staticmethod
        def get_available_providers() -> list[str]:
            return ["CPUExecutionProvider"]

        @staticmethod
        def get_device() -> str:
            return "CPU"

    monkeypatch.setenv("MUSETALK_REQUIRE_CUDA_PROVIDER", "1")
    monkeypatch.setitem(sys.modules, "onnxruntime", _FakeOrt)

    with pytest.raises(RuntimeError) as excinfo:
        entrypoint._assert_onnxruntime_cuda_provider()

    message = str(excinfo.value)
    assert "stage=provider_setup" in message
    assert "missing_cuda_execution_provider" in message
    assert "CPUExecutionProvider" in message


@pytest.mark.integration
def test_service_provider_check_requires_cuda_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeOrt:
        @staticmethod
        def get_available_providers() -> list[str]:
            return ["CPUExecutionProvider"]

        @staticmethod
        def get_device() -> str:
            return "CPU"

    monkeypatch.setenv("MUSETALK_REQUIRE_CUDA_PROVIDER", "1")
    monkeypatch.setitem(sys.modules, "onnxruntime", _FakeOrt)

    with pytest.raises(RuntimeError) as excinfo:
        service._assert_onnxruntime_cuda_provider()

    message = str(excinfo.value)
    assert "stage=provider_setup" in message
    assert "missing_cuda_execution_provider" in message
    assert "CPUExecutionProvider" in message


@pytest.mark.integration
def test_musetalk_service_health_reports_ready_cuda_and_model_state(monkeypatch: pytest.MonkeyPatch) -> None:
    was_loaded = service._models_loaded.is_set()
    if not was_loaded:
        service._models_loaded.set()
    monkeypatch.setattr(service, "_models_error", None)
    monkeypatch.setattr(service, "_cuda_available", True)
    monkeypatch.setattr(service, "_cuda_device", "cuda:0")
    monkeypatch.setattr(service, "_model_load_started_at", 1710000000.0)
    monkeypatch.setattr(service, "_model_load_finished_at", 1710000123.0)
    monkeypatch.setattr(service, "_model_load_seconds", 123.4)
    monkeypatch.setattr(service, "_provider_diagnostics", {"cuda_provider_available": True})
    monkeypatch.setattr(service, "_cuda_memory_snapshot", lambda: {"cuda_total_mib": 4096.0})
    try:
        payload = service._health_payload()
    finally:
        if not was_loaded:
            service._models_loaded.clear()

    assert payload["status"] == "ready"
    assert payload["process_alive"] is True
    assert payload["models_loaded"] is True
    assert payload["cuda_available"] is True
    assert payload["cuda_device"] == "cuda:0"
    assert payload["ready_for_inference"] is True
    assert payload["model_load_seconds"] == 123.4
    assert payload["provider_diagnostics"] == {"cuda_provider_available": True}


@pytest.mark.integration
def test_musetalk_service_debug_sidecar_marks_warm_model_load_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "out.mp4"
    source.write_bytes(b"source")
    audio.write_bytes(b"audio")
    output.write_bytes(b"video")
    monkeypatch.setattr(service, "_model_load_seconds", 222.2)

    service._write_debug_sidecar(
        output_path=output,
        source_image=str(source),
        source_video="",
        selected_source=str(source),
        audio_path=str(audio),
        params={
            "batch_size": 8,
            "use_float16": True,
            "version": "v15",
            "left_cheek_width": 72,
            "right_cheek_width": 72,
            "preview_fast_mode": True,
            "preview_max_width": 384,
            "target_frame_count": 24,
            "target_duration_seconds": 1.5,
            "chunk_max_seconds": 15.0,
        },
        run={
            "run_id": "warm-run",
            "started_epoch": 1710000000.0,
            "source_image_sha256": service._sha256_file(source),
            "source_sha256": service._sha256_file(source),
            "audio_sha256": service._sha256_file(audio),
        },
        stage_timings={"model_load_seconds": 0.0, "inference_loop_seconds": 0.2},
        elapsed_seconds=1.3,
        runtime_info={
            "device": "cuda:0",
            "use_float16": True,
            "requested_batch_size": 8,
            "batch_size": 8,
            "version": "v15",
            "chunk_count": 1,
            "preview_fast_source_path": str(source),
            "source_resolution_before": {"width": 768, "height": 768, "nb_frames": 24},
            "source_resolution_after": {"width": 384, "height": 384, "nb_frames": 24},
            "source_preprocessing": {"enabled": True, "used": True},
            "per_frame_timings": {
                "face_landmark_seconds_per_frame": 0.1,
                "inference_loop_seconds_per_frame": 0.01,
                "frame_count": 24,
            },
        },
    )

    payload = json.loads(service._debug_sidecar_path(output).read_text(encoding="utf-8"))
    assert payload["route"] == "service"
    assert payload["musetalk_run_id"] == "warm-run"
    assert payload["input_reference_image_sha256"] == service._sha256_file(source)
    assert payload["input_audio_sha256"] == service._sha256_file(audio)
    assert payload["model_load_seconds"] == 0.0
    assert payload["service_model_load_seconds"] == 222.2
    assert payload["stage_timings"]["model_load_seconds"] == 0.0
    assert payload["runtime_settings"]["use_float16"] is True
    assert payload["runtime_settings"]["left_cheek_width"] == 72
    assert payload["runtime_settings"]["right_cheek_width"] == 72
    assert payload["preview_fast_source_path"] == str(source)
    assert payload["source_resolution_before"]["width"] == 768
    assert payload["source_resolution_after"]["width"] == 384
    assert payload["chunk_count"] == 1
    assert payload["per_frame_timings"]["frame_count"] == 24


@pytest.mark.integration
def test_musetalk_service_prepare_preview_fast_source_uses_scaled_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "handoff.mp4"
    source.write_bytes(b"video")
    calls: list[dict[str, object]] = []

    def fake_run_ffmpeg_stage(*, stage_name: str, command: list[str], expected_output: Path | None = None, timeout_seconds: float | None = None):
        calls.append({"stage_name": stage_name, "command": command, "timeout_seconds": timeout_seconds})
        assert expected_output is not None
        expected_output.write_bytes(b"scaled-video")

    monkeypatch.setattr(service, "_run_ffmpeg_stage", fake_run_ffmpeg_stage)
    monkeypatch.setattr(
        service,
        "_probe_media",
        lambda path: {"path": str(path), "exists": Path(path).exists(), "width": 768 if Path(path) == source else 384, "height": 768 if Path(path) == source else 384},
    )

    prepared, info = service._prepare_preview_fast_source(
        source_path=source,
        source_kind="video",
        work_dir=tmp_path,
        params={"preview_fast_mode": True, "preview_max_width": 384},
    )

    assert prepared.name == "preview_source_fast.mp4"
    assert prepared.exists()
    assert info["used"] is True
    assert info["source_resolution_before"]["width"] == 768
    assert info["source_resolution_after"]["width"] == 384
    assert calls[0]["stage_name"] == "preview_fast_source_prepare"


@pytest.mark.integration
def test_musetalk_service_face_parser_uses_request_cheek_widths(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeFaceParser:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []
            self.cheek_mask = None

        def _create_cheek_mask(self, *, left_cheek_width: int, right_cheek_width: int):
            self.calls.append((left_cheek_width, right_cheek_width))
            return {"left": left_cheek_width, "right": right_cheek_width}

    fake = FakeFaceParser()
    monkeypatch.setattr(service, "_fp_cheek_widths", (90, 90))

    service._configure_face_parser_for_request(fake, left_cheek_width=72, right_cheek_width=72)

    assert fake.calls == [(72, 72)]
    assert fake.cheek_mask == {"left": 72, "right": 72}
    assert service._fp_cheek_widths == (72, 72)


@pytest.mark.integration
def test_entrypoint_run_ffmpeg_stage_timeout_reports_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(command, stdout=None, stderr=None, text=True, check=False, timeout=None):
        raise subprocess.TimeoutExpired(command, float(timeout or 1.0))

    monkeypatch.setattr(entrypoint.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        entrypoint._run_ffmpeg_stage(
            stage_name="chunk_audio_prepare_0",
            command=["ffmpeg", "-i", "in.wav", "out.wav"],
            timeout_seconds=3.0,
        )

    message = str(excinfo.value)
    assert "musetalk_stage_timeout stage=chunk_audio_prepare_0" in message
    assert "ffmpeg -i in.wav out.wav" in message


@pytest.mark.integration
def test_build_chunk_ranges_splits_long_durations() -> None:
    ranges = entrypoint._build_chunk_ranges(total_duration_seconds=12.2, max_chunk_seconds=5.0)

    assert len(ranges) == 3
    assert ranges[0] == (0.0, 5.0)
    assert ranges[1] == (5.0, 5.0)
    assert ranges[2][0] == 10.0
    assert ranges[2][1] == pytest.approx(2.2, abs=1e-6)


@pytest.mark.integration
def test_runtime_cleanup_returns_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entrypoint.gc, "collect", lambda: 9)
    monkeypatch.setattr(entrypoint, "_gpu_memory_snapshot", lambda _child_pid=None: {"parent_cuda_allocated_mib": 1.0})

    payload = entrypoint._runtime_cleanup(stage_name="post_inference", include_cuda=False)

    assert payload["stage_name"] == "post_inference"
    assert payload["gc_collected"] == 9
    assert payload["torch_cache_cleared"] is False
    assert payload["memory_snapshot"] == {"parent_cuda_allocated_mib": 1.0}


@pytest.mark.integration
def test_concat_chunk_outputs_fallback_reencode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    chunk_a = tmp_path / "chunk_a.mp4"
    chunk_b = tmp_path / "chunk_b.mp4"
    chunk_a.write_bytes(b"a")
    chunk_b.write_bytes(b"b")

    output_path = tmp_path / "final.mp4"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    calls: list[str] = []

    def _fake_run_ffmpeg_stage(*, stage_name: str, command: list[str], timeout_seconds: float, expected_output: Path | None = None):
        calls.append(stage_name)
        if stage_name == "chunk_concat_copy":
            raise RuntimeError("copy failed")
        if expected_output is not None:
            expected_output.write_bytes(b"video")

    monkeypatch.setattr(entrypoint, "_run_ffmpeg_stage", _fake_run_ffmpeg_stage)

    entrypoint._concat_chunk_outputs(chunk_outputs=[chunk_a, chunk_b], output_path=output_path, work_dir=work_dir)

    assert calls == ["chunk_concat_copy", "chunk_concat_reencode"]
    assert output_path.exists()
    assert output_path.stat().st_size > 0
