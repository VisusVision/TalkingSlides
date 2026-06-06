from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
def test_musetalk_preview_isolated_smoke(tmp_path: Path) -> None:
    if str(os.environ.get("RUN_MUSETALK_PREVIEW_SMOKE", "0")).strip() not in {"1", "true", "yes", "on"}:
        pytest.skip("Set RUN_MUSETALK_PREVIEW_SMOKE=1 to run the isolated MuseTalk smoke test")

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for the MuseTalk smoke test")

    musetalk_home = str(os.environ.get("MUSETALK_HOME", "")).strip()
    if not musetalk_home:
        pytest.skip("Set MUSETALK_HOME to run the isolated MuseTalk smoke test")

    musetalk_home_path = Path(musetalk_home)
    if not musetalk_home_path.exists():
        pytest.skip(f"MUSETALK_HOME does not exist: {musetalk_home_path}")

    source_video = tmp_path / "source.mp4"
    audio_wav = tmp_path / "source.wav"
    output_mp4 = tmp_path / "out.mp4"

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=256x256:rate=12:duration=1.2",
            "-pix_fmt",
            "yuv420p",
            str(source_video),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=16000:duration=1.2",
            str(audio_wav),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )

    script_path = Path("services/scripts/musetalk_preview_isolated.py")
    proc = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--musetalk_home",
            str(musetalk_home_path),
            "--source_video",
            str(source_video),
            "--audio_path",
            str(audio_wav),
            "--output_path",
            str(output_mp4),
            "--timeout_seconds",
            str(os.environ.get("MUSETALK_PREVIEW_SMOKE_TIMEOUT_SECONDS", "25")),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert output_mp4.exists(), "MuseTalk isolated preview did not produce an output MP4"
    assert output_mp4.stat().st_size > 0, "MuseTalk isolated preview output MP4 is empty"

    metadata_path = output_mp4.with_suffix(output_mp4.suffix + ".smoke.json")
    assert metadata_path.exists(), "Smoke metadata was not written"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    motion = dict(metadata.get("motion_validation") or {})
    assert int(motion.get("frame_count") or 0) > 1
    assert "audio_match" in motion
    assert "quality_checks" in motion
