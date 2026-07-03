from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist" / "setup-assistant"
BUILD = ROOT / "build" / "setup-assistant"


def build(spec_name: str, work_name: str) -> None:
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
        check=True,
    )


if __name__ == "__main__":
    build("TalkingSlides-Setup-GUI.spec", "gui")
    build("talkingslides-setup-cli.spec", "cli")
    print(DIST)
