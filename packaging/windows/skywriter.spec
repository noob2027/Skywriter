"""PyInstaller onedir specification for the SKYWriter Windows prototype."""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

REPOSITORY_ROOT = Path(SPECPATH).parents[1]
NOTICES_ROOT = Path(os.environ["SKYWRITER_NOTICES_ROOT"])
ICON_PATH = REPOSITORY_ROOT / "packaging" / "assets" / "skywriter-provisional.ico"
VERSION_PATH = REPOSITORY_ROOT / "packaging" / "windows" / "version_info.txt"

datas = collect_data_files("skywriter", includes=["ui/map/static/**/*"])
datas.extend(
    [
        (str(NOTICES_ROOT), "notices"),
        (str(REPOSITORY_ROOT / "docs" / "windows-installer.md"), "docs"),
    ]
)

analysis = Analysis(
    [str(REPOSITORY_ROOT / "src" / "skywriter" / "__main__.py")],
    pathex=[str(REPOSITORY_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "pymavlink.dialects.v20.ardupilotmega",
        "serial.tools.list_ports_windows",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)

# The Codex bundled workspace runtime places Poppler ahead of System32 on PATH.
# QtCore links to Windows' system ICU by its unversioned name; allowing
# PyInstaller to collect Poppler's private ICU build shadows that system DLL and
# fails at runtime with a missing-procedure error. The accepted Windows payload
# and the PySide wheel do not ship these Poppler binaries.
POPPLER_ICU_SHADOWS = {"icudt78.dll", "icuuc.dll"}
analysis.binaries = [
    entry
    for entry in analysis.binaries
    if not (
        Path(entry[0]).name.casefold() in POPPLER_ICU_SHADOWS
        and "poppler" in {part.casefold() for part in Path(entry[1]).parts}
    )
]
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="SKYWriter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH),
    version=str(VERSION_PATH),
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SKYWriter",
    contents_directory="_internal",
)
