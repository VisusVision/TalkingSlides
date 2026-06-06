from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from worker import bootstrap_musetalk as bootstrap  # noqa: E402


POSIX_PROC = os.name != "nt" and Path("/proc").exists()


def _sleeping_musetalk_process(*, marker: str | None) -> subprocess.Popen[str]:
    env = os.environ.copy()
    if marker:
        env["AVATAR_MUSETALK_WARMUP_ID"] = marker
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            "musetalk_entrypoint.py",
            marker or "unrelated",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)


@pytest.mark.skipif(not POSIX_PROC, reason="MuseTalk warmup process-group cleanup is Linux /proc based")
def test_warmup_timeout_kills_child_process_too(tmp_path: Path) -> None:
    marker = f"test-warmup-timeout-{os.getpid()}-{time.time_ns()}"
    parent_script = tmp_path / "fake_parent_shell.py"
    parent_script.write_text(
        "\n".join(
            [
                "import os",
                "import subprocess",
                "import sys",
                "import time",
                "marker = sys.argv[1]",
                "env = os.environ.copy()",
                "env['AVATAR_MUSETALK_WARMUP_ID'] = marker",
                "subprocess.Popen([",
                "    sys.executable,",
                "    '-c',",
                "    'import time; time.sleep(60)',",
                "    'musetalk_entrypoint.py',",
                "    marker,",
                "], env=env)",
                "time.sleep(60)",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AVATAR_MUSETALK_WARMUP_ID"] = marker
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(parent_script))} {shlex.quote(marker)}"

    try:
        result = bootstrap._run_musetalk_warmup_shell_command(
            command=command,
            env=env,
            timeout_seconds=1.0,
            warmup_id=marker,
        )
        assert result.timed_out is True
        assert result.cleanup.get("terminated") or result.cleanup.get("killed")
        remaining_marked = [
            match for match in bootstrap._find_musetalk_warmup_processes(warmup_id=marker)
            if match.marked
        ]
        assert remaining_marked == []
    finally:
        bootstrap._check_and_kill_musetalk_warmup_orphans(warmup_id=marker)


def test_bootstrap_treats_warmup_timeout_as_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    liveportrait_home = tmp_path / "liveportrait"
    liveportrait_home.mkdir()
    liveportrait_entrypoint = liveportrait_home / "inference.py"
    liveportrait_entrypoint.write_text("print('ok')\n", encoding="utf-8")
    liveportrait_model_root = tmp_path / "liveportrait-models"
    liveportrait_model_root.mkdir()

    musetalk_home = tmp_path / "musetalk"
    musetalk_home.mkdir()
    model_root = tmp_path / "models"
    required_model_files = [
        model_root / "sd-vae" / "config.json",
        model_root / "sd-vae" / "diffusion_pytorch_model.bin",
        model_root / "musetalkV15" / "unet.pth",
        model_root / "whisper" / "config.json",
        model_root / "whisper" / "pytorch_model.bin",
        model_root / "whisper" / "preprocessor_config.json",
        model_root / "dwpose" / "dw-ll_ucoco_384.pth",
        model_root / "face-parse-bisent" / "79999_iter.pth",
        model_root / "face-parse-bisent" / "resnet18-5c106cde.pth",
    ]
    for path in required_model_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    monkeypatch.setenv("AVATAR_ENGINE", "liveportrait+musetalk")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_HOME", str(liveportrait_home))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_ENTRYPOINT", str(liveportrait_entrypoint))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_MODEL_PATH", str(liveportrait_model_root))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", f"python {liveportrait_entrypoint}")
    monkeypatch.setenv("AVATAR_PREVIEW_USE_LIVEPORTRAIT", "1")
    monkeypatch.setenv("AVATAR_PREVIEW_USE_MUSETALK", "1")
    monkeypatch.setenv("AVATAR_PREVIEW_USE_RESTORATION", "0")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "python /app/scripts/musetalk_entrypoint.py")
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "1")
    monkeypatch.setenv("MUSETALK_HOME", str(musetalk_home))
    monkeypatch.setenv("MUSETALK_MODEL_PATH", str(model_root))

    real_which = bootstrap.shutil.which

    def fake_which(name: str) -> str | None:
        if name in {"ffmpeg", "ffprobe"}:
            return f"/usr/bin/{name}"
        return real_which(name)

    monkeypatch.setattr(bootstrap.shutil, "which", fake_which)
    monkeypatch.setattr(bootstrap, "_check_runtime_imports", lambda: {"python": "3.10.0"})
    monkeypatch.setattr(bootstrap, "_start_musetalk_service", lambda **_: False)
    monkeypatch.setattr(bootstrap, "_warmup_musetalk", lambda **_: 70)

    assert bootstrap.main() == 0


@pytest.mark.skipif(not POSIX_PROC, reason="MuseTalk warmup orphan scan is Linux /proc based")
def test_orphan_check_detects_and_kills_marked_warmup_process() -> None:
    marker = f"test-warmup-orphan-{os.getpid()}-{time.time_ns()}"
    proc = _sleeping_musetalk_process(marker=marker)
    try:
        time.sleep(0.3)
        result = bootstrap._check_and_kill_musetalk_warmup_orphans(warmup_id=marker)
        assert result.warmup_orphan_count >= 1
        assert result.killed_count >= 1
        assert result.remaining_count == 0
        proc.wait(timeout=3.0)
    finally:
        _terminate_process(proc)
        bootstrap._check_and_kill_musetalk_warmup_orphans(warmup_id=marker)


@pytest.mark.skipif(not POSIX_PROC, reason="MuseTalk warmup orphan scan is Linux /proc based")
def test_orphan_check_does_not_kill_unrelated_musetalk_process() -> None:
    marker = f"test-warmup-unrelated-{os.getpid()}-{time.time_ns()}"
    proc = _sleeping_musetalk_process(marker=None)
    try:
        time.sleep(0.3)
        result = bootstrap._check_and_kill_musetalk_warmup_orphans(warmup_id=marker)
        assert result.warmup_orphan_count == 0
        assert result.remaining_count == 0
        assert proc.poll() is None
    finally:
        _terminate_process(proc)
