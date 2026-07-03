from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).resolve()
    appdir = os.environ.get("APPDIR")
    if appdir and os.environ.get("APPIMAGE"):
        candidate = Path(appdir) / "usr" / "share" / "talkingslides-setup"
        if candidate.exists():
            return candidate.resolve()
    return Path(__file__).resolve().parent


def executable_directory() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def asset_path(name: str) -> Path:
    candidates = (
        resource_root() / "assets" / name,
        Path(__file__).resolve().parent / "assets" / name,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
