from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from release import (
    BUILD_VERSION_ENV,
    DEFAULT_DEVELOPMENT_VERSION,
    DISPLAY_VERSION_ENV,
    VERSION_HOOK_ENV,
    validate_package_version,
    write_version_runtime_hook,
)

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist" / "setup-assistant"
BUILD = ROOT / "build" / "setup-assistant"


def package_version() -> str:
    return validate_package_version(
        os.environ.get(BUILD_VERSION_ENV)
        or os.environ.get(DISPLAY_VERSION_ENV)
        or DEFAULT_DEVELOPMENT_VERSION
    )


def build(spec_name: str, work_name: str, version_hook: Path) -> None:
    env = os.environ.copy()
    env[VERSION_HOOK_ENV] = str(version_hook)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD / work_name),
            str(ROOT / "packaging" / "setup_assistant" / spec_name),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    version = package_version()
    hook = write_version_runtime_hook(version, BUILD / "version_runtime_hook.py")
    build("TalkingSlides-Setup-GUI.spec", "gui", hook)
    build("talkingslides-setup-cli.spec", "cli", hook)
    print(f"{DIST} ({version})")
