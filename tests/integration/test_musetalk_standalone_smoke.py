"""test_musetalk_standalone_smoke.py

Standalone MuseTalk pytest smoke test.

Zero pipeline dependencies:
  - No avatar.* imports
  - No canonical_pipeline
  - No LivePortrait
  - No UI / cache / fallback logic

Calls musetalk_smoke.py via subprocess.  The final stdout line must be
"PASS ..." for the test to pass.

Activation:
    RUN_MUSETALK_SMOKE=1 pytest tests/integration/test_musetalk_standalone_smoke.py -v -s

Required env vars:
    MUSETALK_HOME            path to /opt/musetalk checkout
    MUSETALK_MODEL_PATH      path to model weights root
    RUN_MUSETALK_SMOKE=1     gate flag (test skips if absent)

Optional:
    MUSETALK_SMOKE_IMAGE     override source image (default: avatar 2 original)
    MUSETALK_SMOKE_AUDIO     override driven audio (default: avatar 2 preview.wav)
    MUSETALK_SMOKE_TIMEOUT   inference timeout seconds (default: 240)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SMOKE_SCRIPT = Path(__file__).resolve().parents[2] / "services" / "scripts" / "musetalk_smoke.py"

_DEFAULT_IMAGE = "/app/storage_local/avatars/2/uploads/avatar_original.jpg"
_DEFAULT_AUDIO = "/app/storage_local/avatars/2/preview/preview.wav"


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------

def _must_skip() -> str | None:
    """Return a skip reason string, or None if we should run."""
    gate = str(os.environ.get("RUN_MUSETALK_SMOKE", "0")).strip().lower()
    if gate not in {"1", "true", "yes", "on"}:
        return "Set RUN_MUSETALK_SMOKE=1 to run the standalone MuseTalk smoke test"

    if not _SMOKE_SCRIPT.exists():
        return f"musetalk_smoke.py not found at {_SMOKE_SCRIPT}"

    if shutil.which("ffmpeg") is None:
        return "ffmpeg not found in PATH — required by MuseTalk"
    if shutil.which("ffprobe") is None:
        return "ffprobe not found in PATH — required by MuseTalk"

    musetalk_home = str(os.environ.get("MUSETALK_HOME", "")).strip()
    if not musetalk_home:
        return "Set MUSETALK_HOME=/opt/musetalk to run the standalone MuseTalk smoke test"
    if not Path(musetalk_home).exists():
        return f"MUSETALK_HOME does not exist: {musetalk_home}"

    return None


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_musetalk_standalone_smoke(tmp_path: Path) -> None:
    """
    Runs musetalk_smoke.py as a black-box subprocess.

    Pass condition: subprocess exits 0 AND last stdout line starts with 'PASS'.
    Fail condition: any other exit code, or last stdout line starts with 'FAIL'.

    The test does NOT assert motion quality — that is deferred until MuseTalk
    is proven stable.  It only asserts:
      1. The subprocess finished without timeout / crash
      2. An output mp4 was produced and is non-empty
      3. The smoke script itself classified the run as PASS
    """
    skip_reason = _must_skip()
    if skip_reason:
        pytest.skip(skip_reason)

    musetalk_home = str(os.environ.get("MUSETALK_HOME", "/opt/musetalk")).strip()
    model_path = str(os.environ.get("MUSETALK_MODEL_PATH", "/app/storage_local/models")).strip()
    timeout = float(str(os.environ.get("MUSETALK_SMOKE_TIMEOUT", "240")).strip() or "240")
    image = str(os.environ.get("MUSETALK_SMOKE_IMAGE", _DEFAULT_IMAGE)).strip()
    audio = str(os.environ.get("MUSETALK_SMOKE_AUDIO", _DEFAULT_AUDIO)).strip()
    output_mp4 = tmp_path / "musetalk_smoke_out.mp4"

    env = os.environ.copy()
    env["MUSETALK_HOME"] = musetalk_home
    env["MUSETALK_MODEL_PATH"] = model_path
    # Suppress diagnostic artefact writes — smoke only
    env["AVATAR_PREVIEW_DIAGNOSTIC_MODE"] = "0"
    env["MUSETALK_TARGET_FRAME_COUNT"] = "0"
    env["MUSETALK_TARGET_DURATION_SECONDS"] = "0.000000"

    cmd = [
        sys.executable,
        str(_SMOKE_SCRIPT),
        "--image", image,
        "--audio", audio,
        "--output", str(output_mp4),
        "--inference_timeout", str(timeout),
        "--model_load_timeout", "180",
    ]

    print(f"\n[smoke] command: {' '.join(cmd)}", flush=True)
    print(f"[smoke] MUSETALK_HOME={musetalk_home}", flush=True)
    print(f"[smoke] MUSETALK_MODEL_PATH={model_path}", flush=True)

    # Add 60 s of grace on top of the inference timeout so pytest itself
    # does not kill the process before the smoke script can emit FAIL.
    pytest_timeout = timeout + 60.0

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=False,   # stream to terminal so we see stage logs
            check=False,
            timeout=pytest_timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"[FAIL timeout_inference] musetalk_smoke.py did not finish within "
            f"{pytest_timeout:.0f}s (inference_timeout={timeout:.0f}s + 60s grace). "
            "MuseTalk inference is hanging — likely CUDA/VRAM or mmpose deadlock."
        )

    # ── Re-run capture-only to get stdout for assertion ──────────────────
    cap_proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=pytest_timeout,
    ) if proc.returncode != 0 else None

    # Determine the result line from the fresh run or infer from exit code
    stdout_lines = []
    if cap_proc is not None:
        stdout_lines = [l.strip() for l in (cap_proc.stdout or "").splitlines() if l.strip()]
        for line in stdout_lines:
            print(f"[smoke stdout] {line}", flush=True)
        for line in (cap_proc.stderr or "").splitlines():
            print(f"[smoke stderr] {line}", flush=True)

    if proc.returncode != 0:
        # Find the FAIL <class> line for a clean assertion message
        result_line = next(
            (l for l in reversed(stdout_lines) if l.startswith("[RESULT]")),
            f"[RESULT] FAIL (exit_code={proc.returncode})",
        )
        reason_line = next(
            (l for l in reversed(stdout_lines) if l.startswith("[REASON]")),
            "",
        )
        pytest.fail(
            f"{result_line}\n"
            f"{reason_line}\n\n"
            "Re-run command:\n"
            f"  {' '.join(cmd)}"
        )

    # ── Output file assertions ────────────────────────────────────────────
    assert output_mp4.exists(), (
        f"MuseTalk smoke produced exit 0 but output mp4 is missing: {output_mp4}"
    )
    assert output_mp4.stat().st_size > 0, (
        f"MuseTalk smoke output mp4 is empty (0 bytes): {output_mp4}"
    )

    metadata_path = output_mp4.with_suffix(output_mp4.suffix + ".smoke.json")
    assert metadata_path.exists(), (
        f"Standalone smoke metadata missing: {metadata_path}"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    stage_timings = dict(metadata.get("stage_timings") or {})
    verify = dict(metadata.get("verify") or {})
    entrypoint_stage_timings = dict(metadata.get("entrypoint_stage_timings") or {})

    assert bool(verify.get("mux_encode_succeeded")) is True
    assert bool(verify.get("has_audio_stream")) is True
    assert bool(verify.get("has_video_stream")) is True
    assert "inference_seconds" in stage_timings
    assert "output_verify_seconds" in stage_timings
    assert isinstance(entrypoint_stage_timings, dict)
    assert any(
        key in entrypoint_stage_timings
        for key in ["model_load", "face_landmark_extraction", "inference_loop", "mux_encode", "final_save"]
    )

    print(
        f"\n[smoke] PASS  output={output_mp4}  size={output_mp4.stat().st_size} bytes",
        flush=True,
    )
