from __future__ import annotations

import os
from pathlib import Path
import tempfile
import sys

import importlib.util
from pathlib import Path


def _load_avatar_preview_flow_module():
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


def test_clear_preview_run_artifacts_removes_expected_files(tmp_path: Path) -> None:
    preview_dir = tmp_path
    output_mp4 = preview_dir / "preview.mp4"
    source_mp3 = preview_dir / "preview_source.mp3"
    audio_wav = preview_dir / "preview.wav"
    meta_json = output_mp4.with_suffix(output_mp4.suffix + ".meta.json")
    lp_mp4 = output_mp4.with_suffix(output_mp4.suffix + ".liveportrait.mp4")
    musetalk_mp4 = output_mp4.with_suffix(output_mp4.suffix + ".musetalk.mp4")

    # create files that should be removed
    for f in [output_mp4, meta_json, lp_mp4, musetalk_mp4, source_mp3, audio_wav]:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("dummy")
        assert f.exists()

    removed = apf._clear_preview_run_artifacts(preview_dir=preview_dir, output_mp4=output_mp4, source_mp3=source_mp3, audio_wav=audio_wav)

    # All created files should be removed
    for f in [output_mp4, meta_json, lp_mp4, musetalk_mp4, source_mp3, audio_wav]:
        assert not f.exists()

    assert isinstance(removed, list)
    assert len(removed) >= 1
