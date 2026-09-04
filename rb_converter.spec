# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir + macOS .app with bundled ffmpeg/ffprobe.

Drop static binaries into vendor/ffmpeg/ before building (see README).
"""

from pathlib import Path

root = Path(SPECPATH).resolve()
src = root / "src"
vendor_ffmpeg = root / "vendor" / "ffmpeg"

binaries = []
for name in ("ffmpeg", "ffprobe"):
    path = vendor_ffmpeg / name
    if not path.is_file():
        raise SystemExit(
            f"Missing {path}. Drop static ffmpeg and ffprobe into vendor/ffmpeg/ "
            "(see README)."
        )
    binaries.append((str(path), "."))

datas = []
for lic in ("COPYING", "LICENSE", "LICENSE.md", "COPYING.GPLv3"):
    lic_path = vendor_ffmpeg / lic
    if lic_path.is_file():
        datas.append((str(lic_path), "."))

a = Analysis(
    [str(src / "rb_converter_gui.py")],
    pathex=[str(src)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Rekordbox WAV Converter",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Rekordbox WAV Converter",
)

app = BUNDLE(
    coll,
    name="Rekordbox WAV Converter.app",
    icon=None,
    bundle_identifier="com.rekordbox.wav.converter",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleDisplayName": "Rekordbox WAV Converter",
        "CFBundleShortVersionString": "1.0.0",
        "NSDocumentsFolderUsageDescription": (
            "Writes converted WAV files and the Rekordbox import XML."
        ),
    },
)
