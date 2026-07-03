# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
import sys

ROOT = Path(SPECPATH).parents[1]
ASSET = ROOT / "tools" / "setup_assistant" / "assets" / "talkingslides-setup.svg"
CLI_NAME = "talkingslides-setup-cli" if sys.platform.startswith("win") else "talkingslides-setup"
VERSION_HOOK = os.environ.get("SETUP_ASSISTANT_VERSION_HOOK")

analysis = Analysis(
    [str(ROOT / "packaging" / "setup_assistant" / "cli_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ASSET), "tools/setup_assistant/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[VERSION_HOOK] if VERSION_HOOK else [],
    excludes=["PySide6", "tools.setup_assistant.gui"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name=CLI_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
