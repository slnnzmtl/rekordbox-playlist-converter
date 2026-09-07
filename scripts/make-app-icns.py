#!/usr/bin/env python3
"""Build assets/app.icns and the 256px Tk icon from assets/rpc-logo-white.png.

Requires ffmpeg and macOS iconutil.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "rpc-logo-white.png"
DEST = ROOT / "assets" / "app.icns"
WINDOW_ICON = ROOT / "assets" / "rpc-logo-white-256.png"
WINDOW_ICON_SIZE = 256

# Apple iconset filenames → pixel size (1x and @2x).
_ICONSET_SIZES = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)


def _require_cmd(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"missing command: {name}")


def _png_at_size(src: Path, dest: Path, size: int, *, rgba: bool) -> None:
    # iconutil rejects many RGB-only PNGs; force RGBA for the iconset.
    vf = f"scale={size}:{size}:flags=lanczos"
    if rgba:
        vf += ",format=rgba"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vf", vf, str(dest)],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).decode("utf-8", "replace")
        raise SystemExit(f"ffmpeg failed for {size}px: {err}")
    if not dest.read_bytes().startswith(b"\x89PNG"):
        raise SystemExit(f"{dest} is not a PNG")


def main() -> int:
    if not SRC.is_file():
        raise SystemExit(f"Missing {SRC}")
    _require_cmd("ffmpeg")
    _require_cmd("iconutil")

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "app.iconset"
        iconset.mkdir()
        for name, size in _ICONSET_SIZES:
            _png_at_size(SRC, iconset / name, size, rgba=True)
        out = Path(tmp) / "app.icns"
        proc = subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).decode("utf-8", "replace")
            raise SystemExit(f"iconutil failed: {err}")
        data = out.read_bytes()
        if not data.startswith(b"icns"):
            raise SystemExit(f"{out} is not an icns file")
        DEST.write_bytes(data)

    print(f"Wrote {DEST} ({DEST.stat().st_size} bytes)")
    _png_at_size(SRC, WINDOW_ICON, WINDOW_ICON_SIZE, rgba=False)
    print(f"Wrote {WINDOW_ICON} ({WINDOW_ICON.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
