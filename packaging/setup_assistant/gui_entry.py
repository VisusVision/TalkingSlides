from __future__ import annotations

import os
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.setup_assistant.gui import launch_gui


if __name__ == "__main__":
    smoke = "--smoke" in sys.argv
    if smoke and sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    raise SystemExit(launch_gui(smoke=smoke))
