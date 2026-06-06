from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar import canonical_adapters as adapters  # noqa: E402
from avatar import canonical_pipeline as pipeline  # noqa: E402
from avatar import pipeline as avatar_pipeline  # noqa: E402
from services.scripts import musetalk_entrypoint as entrypoint  # noqa: E402
from services.scripts import musetalk_runner  # noqa: E402


def _gpu_resources(total_mib: int, free_mib: int) -> dict[str, object]:
    return {
        "gpu": {
            "available": True,
            "selected": {
                "name": "NVIDIA GeForce RTX 3050 Laptop GPU",
                "total_mib": int(total_mib),
                "free_mib": int(free_mib),
            },
        },
        "system": {},
    }


def _request(tmp_path: Path, *, duration: float = 37.375, frames: int = 598) -> avatar_pipeline.AvatarRenderRequest:
    return avatar_pipeline.AvatarRenderRequest(
        source_image_path=str(tmp_path / "face.png"),
        audio_path=str(tmp_path / "preview.wav"),
        output_path=str(tmp_path / "preview.mp4"),
        preview_teacher_id=2,
        preview_job_id=11,
        target_duration_seconds=float(duration),
        target_frame_count=int(frames),
    )


def test_musetalk_timeout_uses_low_vram_history_for_known_workload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    metrics_path = tmp_path / "avatar_stage_metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "version": 1,
                "stages": {
                    "musetalk": [
                        {
                            "success": True,
                            "elapsed_seconds": 2850.65,
                            "audio_duration_seconds": 37.375,
                            "frame_count": 598,
                            "resources": {"gpu": {"selected": {"total_mib": 4096, "free_mib": 2334}}},
                            "context": {
                                "chunk_count": 3,
                                "per_chunk_timings": [
                                    {"chunk_index": 0, "total_seconds": 1487.67},
                                    {"chunk_index": 1, "total_seconds": 697.5},
                                    {"chunk_index": 2, "total_seconds": 665.48},
                                ],
                            },
                        }
                    ]
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AVATAR_ORCH_METRICS_FILE", str(metrics_path))
    monkeypatch.setenv("MUSETALK_CHUNK_MAX_SECONDS", "15")
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_MAX_SECONDS", "7200")
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_SAFETY_MULTIPLIER", "1.4")
    monkeypatch.delenv("AVATAR_PREVIEW_STAGE_TIMEOUT_MUSETALK_SECONDS", raising=False)
    monkeypatch.delenv("AVATAR_PREVIEW_MUSETALK_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AVATAR_ORCH_STAGE_TIMEOUT_MUSETALK_SECONDS", raising=False)

    budget, reason = pipeline._preview_musetalk_timeout_profile(
        _request(tmp_path),
        resources=_gpu_resources(4096, 2334),
        contract_duration_seconds=37.375,
    )

    assert budget > 2851.0
    assert 3600.0 <= budget <= 4200.0
    assert reason["chunk_count"] == 3
    assert reason["history_sample_count"] == 1
    assert reason["history_max_seconds"] == pytest.approx(2850.65)
    assert reason["per_chunk_timeout_seconds"] >= 1800.0


def test_musetalk_explicit_timeout_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVATAR_PREVIEW_STAGE_TIMEOUT_MUSETALK_SECONDS", "333")
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_MAX_SECONDS", "7200")

    budget, reason = pipeline._preview_musetalk_timeout_profile(
        _request(tmp_path, duration=3.0, frames=48),
        resources=_gpu_resources(4096, 3000),
        contract_duration_seconds=3.0,
    )

    assert budget == 333.0
    assert reason["source"] == "explicit"
    assert reason["explicit_source_env"] == "AVATAR_PREVIEW_STAGE_TIMEOUT_MUSETALK_SECONDS"


def test_musetalk_low_vram_multiplier_is_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_HISTORY_ENABLED", "0")
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_SAFETY_MULTIPLIER", "1.0")
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_LOW_VRAM_MULTIPLIER", "2.0")
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_MAX_SECONDS", "7200")

    request = _request(tmp_path, duration=5.0, frames=80)
    low_budget, low_reason = pipeline._preview_musetalk_timeout_profile(
        request,
        resources=_gpu_resources(4096, 3000),
        contract_duration_seconds=5.0,
    )
    high_budget, high_reason = pipeline._preview_musetalk_timeout_profile(
        request,
        resources=_gpu_resources(8192, 7000),
        contract_duration_seconds=5.0,
    )

    assert low_reason["low_vram"] is True
    assert high_reason["low_vram"] is False
    assert low_budget == pytest.approx(high_budget * 2.0)


def test_adapter_timeout_cleanup_terminates_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(adapters.os, "name", "posix", raising=False)
    monkeypatch.setattr(adapters.os, "getpgid", lambda pid: 4321, raising=False)
    monkeypatch.setattr(adapters.os, "killpg", lambda pgid, sig: calls.append((str(sig), int(pgid))), raising=False)

    payload = adapters._terminate_process_group(FakeProcess(), stage_name="preview_musetalk", reason="timeout")

    assert payload["terminated"] is True
    assert payload["pgid"] == 4321
    assert calls == [(str(adapters.signal.SIGTERM), 4321)]


def test_timeout_classification_distinguishes_idle_chunk_total() -> None:
    assert (
        entrypoint._classify_progress_timeout(
            now=20.0,
            last_progress_at=1.0,
            idle_timeout_seconds=10.0,
            total_deadline=None,
            chunk_deadline=None,
        )
        == "musetalk_idle_timeout"
    )
    assert (
        entrypoint._classify_progress_timeout(
            now=20.0,
            last_progress_at=19.0,
            idle_timeout_seconds=10.0,
            total_deadline=None,
            chunk_deadline=18.0,
        )
        == "musetalk_chunk_timeout"
    )
    assert (
        entrypoint._classify_progress_timeout(
            now=20.0,
            last_progress_at=19.0,
            idle_timeout_seconds=10.0,
            total_deadline=18.0,
            chunk_deadline=25.0,
        )
        == "musetalk_total_timeout"
    )


def test_late_output_is_not_accepted_as_current_run(tmp_path: Path) -> None:
    output = tmp_path / "preview.mp4.musetalk.mp4"
    output.write_bytes(b"late")
    os.utime(output, (1000.0, 1000.0))

    valid, reason = musetalk_runner._validate_current_run_output(
        output_path=output,
        run_id="current",
        started_epoch=2000.0,
        expected_source_sha256="source",
        expected_audio_sha256="audio",
    )

    assert valid is False
    assert reason == "late_musetalk_output_detected:older_than_current_run"


def test_sidecar_run_id_mismatch_is_not_accepted(tmp_path: Path) -> None:
    output = tmp_path / "preview.mp4.musetalk.mp4"
    output.write_bytes(b"video")
    sidecar = musetalk_runner._debug_sidecar_path(output)
    sidecar.write_text(json.dumps({"musetalk_run_id": "old"}), encoding="utf-8")

    valid, reason = musetalk_runner._validate_current_run_output(
        output_path=output,
        run_id="current",
        started_epoch=0.0,
        expected_source_sha256="source",
        expected_audio_sha256="audio",
    )

    assert valid is False
    assert reason == "late_musetalk_output_detected:run_id_mismatch"
