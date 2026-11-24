# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "data"), "data"),
    (str(ROOT / "profiles"), "profiles"),
    (str(ROOT / "src" / "animforge"), "animforge_src/animforge"),
]

block_cipher = None

a = Analysis(
    [str(ROOT / "src" / "animforge" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "animforge",
        "animforge.cli",
        "animforge.gui",
        "animforge.action_matcher",
        "animforge.charcodes",
        "animforge.config",
        "animforge.constants",
        "animforge.crilayla",
        "animforge.xfbin_scanner",
        "animforge.math_retarget",
        "animforge.cleanup",
        "animforge.builders",
        "animforge.validation",
        "animforge.fixer",
        "animforge.pack_manifest",
        "animforge.studio_postprocess",
        "animforge.blender_runner",
        "tkinter",
        "tkinter.ttk",
        "tkinter.scrolledtext",
        "tkinter.filedialog",
        "tkinter.messagebox",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AnimForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ROOT / "packaging" / "animforge.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
    manifest=str(ROOT / "packaging" / "app.manifest"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AnimForge",
)
