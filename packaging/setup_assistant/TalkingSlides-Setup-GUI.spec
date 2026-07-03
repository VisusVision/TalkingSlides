# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
ASSET = ROOT / "tools" / "setup_assistant" / "assets" / "talkingslides-setup.svg"

analysis = Analysis(
    [str(ROOT / "packaging" / "setup_assistant" / "gui_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ASSET), "tools/setup_assistant/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="TalkingSlides-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
