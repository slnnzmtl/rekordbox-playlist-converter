# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir + universal2 macOS .app with bundled ffmpeg/ffprobe.

Needs python.org universal2 Python and fat static ffmpeg/ffprobe (see README).
"""

import subprocess
from pathlib import Path

root = Path(SPECPATH).resolve()
src = root / "src"
vendor_ffmpeg = root / "vendor" / "ffmpeg"


def require_universal2(path: Path) -> None:
    """Abort unless path is a fat Mach-O with x86_64 and arm64."""
    try:
        proc = subprocess.run(
            ["lipo", "-archs", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise SystemExit(
            f"lipo not found; build the .app on macOS after lipo'ing {path.name} "
            "(see README)."
        ) from None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise SystemExit(f"lipo failed for {path}: {err}")
    arches = set(proc.stdout.split())
    missing = {"x86_64", "arm64"} - arches
    if missing:
        have = proc.stdout.strip() or "none"
        raise SystemExit(
            f"{path} is not universal2 (archs: {have}; need x86_64 and arm64). "
            "See README."
        )


binaries = []
for name in ("ffmpeg", "ffprobe"):
    path = vendor_ffmpeg / name
    if not path.is_file():
        raise SystemExit(
            f"Missing {path}. Lipo static ffmpeg and ffprobe into vendor/ffmpeg/ "
            "(see README)."
        )
    require_universal2(path)
    binaries.append((str(path), "."))

# Always ship project GPL + ffmpeg GPL text (do not rely on upstream zips).
datas = []
project_license = root / "LICENSE"
if not project_license.is_file():
    raise SystemExit(f"Missing {project_license}")
datas.append((str(project_license), "."))

ffmpeg_license = vendor_ffmpeg / "COPYING.GPLv3"
if not ffmpeg_license.is_file():
    # Fallback if build script has not run ensure_ffmpeg_license yet.
    ffmpeg_license = root / "third_party" / "ffmpeg" / "COPYING.GPLv3"
if not ffmpeg_license.is_file():
    raise SystemExit(
        f"Missing ffmpeg license at vendor/ffmpeg/COPYING.GPLv3 or "
        f"{root / 'third_party' / 'ffmpeg' / 'COPYING.GPLv3'}"
    )
datas.append((str(ffmpeg_license), "third_party/ffmpeg"))

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
    target_arch="universal2",
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
    bundle_identifier="io.github.slnnzmtl.rekordboxWavConverter",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleDisplayName": "Rekordbox WAV Converter",
        "CFBundleShortVersionString": "1.0.0",
        "NSDocumentsFolderUsageDescription": (
            "Writes converted WAV files and the Rekordbox import XML."
        ),
    },
)
