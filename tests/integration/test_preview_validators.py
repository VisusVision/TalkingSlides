from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import sys

import pytest

import importlib.util


def _load_avatar_preview_flow_module():
    # Import the module directly from file to avoid importing the full
    # services.worker package (which triggers Django setup in tests).
    repo_root = Path(__file__).resolve().parents[2]
    services_root = repo_root / "services"
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))
    module_path = repo_root / "services" / "worker" / "avatar_preview_flow.py"
    spec = importlib.util.spec_from_file_location("avatar_preview_flow", str(module_path))
    apf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(apf)
    return apf


apf = _load_avatar_preview_flow_module()


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.integration
def test_check_near_static_detects_static_and_dynamic(tmp_path: Path) -> None:
    if not _has_ffmpeg():
        pytest.skip("ffmpeg/ffprobe not available")

    # Create a tiny static video from a solid color source.
    static_mp4 = tmp_path / "static.mp4"
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(static_mp4)
    ], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0 and static_mp4.exists()

    # Create a dynamic test video (testsrc2)
    dynamic_mp4 = tmp_path / "dynamic.mp4"
    proc2 = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=128x128:rate=25:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dynamic_mp4)
    ], capture_output=True, text=True, timeout=30)
    assert proc2.returncode == 0 and dynamic_mp4.exists()

    # static should be detected as near-static
    assert apf._check_near_static(static_mp4) is True

    # dynamic should NOT be detected as near-static
    assert apf._check_near_static(dynamic_mp4) is False


@pytest.mark.integration
def test_probe_frame_count_and_duration(tmp_path: Path) -> None:
    if not _has_ffmpeg():
        pytest.skip("ffmpeg/ffprobe not available")

    # Create a short dynamic clip
    mp4 = tmp_path / "clip.mp4"
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=128x128:rate=25:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mp4)
    ], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0 and mp4.exists()

    frames = apf._probe_frame_count(mp4)
    dur = apf._probe_duration_seconds(mp4)
    assert frames >= 1
    assert dur >= 1.0
