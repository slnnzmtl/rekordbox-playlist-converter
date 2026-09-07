#!/usr/bin/env python3
"""Build assets/app.icns from assets/rpc-logo-white.png (requires ffmpeg)."""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "rpc-logo-white.png"
DEST = ROOT / "assets" / "app.icns"

# PNG-compressed ICNS types used by modern macOS.
_ENTRIES = (
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),
    ("ic12", 64),
    ("ic13", 256),
    ("ic14", 512),
)


def _png_at_size(src: Path, dest: Path, size: int) -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            f"scale={size}:{size}:flags=lanczos",
            str(dest),
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).decode("utf-8", "replace")
        raise SystemExit(f"ffmpeg failed for {size}px: {err}")
    data = dest.read_bytes()
    if not data.startswith(b"\x89PNG"):
        raise SystemExit(f"{dest} is not a PNG")
    return data


def main() -> int:
    if not SRC.is_file():
        raise SystemExit(f"Missing {SRC}")
    chunks: list[bytes] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for ostype, size in _ENTRIES:
            data = _png_at_size(SRC, tmp_path / f"{ostype}.png", size)
            chunks.append(ostype.encode("ascii") + struct.pack(">I", 8 + len(data)) + data)
    body = b"".join(chunks)
    DEST.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)
    print(f"Wrote {DEST} ({DEST.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
