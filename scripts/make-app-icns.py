#!/usr/bin/env python3
"""Build macOS-style rounded assets from assets/rpc-logo-white.png.

Insets artwork to Apple's 824/1024 icon grid, applies an Apple-like squircle
mask, writes the 256px Tk icon, and builds assets/app.icns (iconutil on macOS,
handmade PNG+ARGB ICNS elsewhere).
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "rpc-logo-white.png"
DEST = ROOT / "assets" / "app.icns"
WINDOW_ICON = ROOT / "assets" / "rpc-logo-white-256.png"
WINDOW_ICON_SIZE = 256

# Superellipse exponent used by modern macOS / iOS app icons.
_SQUIRCLE_N = 5.0

# Apple's macOS app-icon grid (1024 template): artwork fills an 824 box.
_ICON_GRID = 1024
_ICON_ART_BOX = 824

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

# Handmade ICNS PNG types when iconutil is unavailable.
_ICNS_PNG = (
    ("ic11", 32),
    ("ic12", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic13", 256),
    ("ic09", 512),
    ("ic14", 512),
    ("ic10", 1024),
)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png_rgba(path: Path) -> tuple[int, int, bytearray]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SystemExit(f"{path} is not a PNG")
    width = height = None
    color_type = None
    idat = bytearray()
    i = 8
    while i + 8 <= len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        tag = data[i + 4 : i + 8]
        payload = data[i + 8 : i + 8 + length]
        i += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if width is None or color_type not in (2, 6) or bit_depth != 8:
        raise SystemExit(f"{path} is not an 8-bit RGB/RGBA PNG")
    raw = zlib.decompress(bytes(idat))
    bpp = 4 if color_type == 6 else 3
    stride = width * bpp
    rows: list[bytes] = []
    src = 0
    prev = bytes(stride)
    for _ in range(height):
        filt = raw[src]
        scan = bytearray(raw[src + 1 : src + 1 + stride])
        src += 1 + stride
        if filt == 1:
            for x in range(bpp, stride):
                scan[x] = (scan[x] + scan[x - bpp]) & 255
        elif filt == 2:
            for x in range(stride):
                scan[x] = (scan[x] + prev[x]) & 255
        elif filt == 3:
            for x in range(stride):
                left = scan[x - bpp] if x >= bpp else 0
                scan[x] = (scan[x] + ((left + prev[x]) // 2)) & 255
        elif filt == 4:
            for x in range(stride):
                a = scan[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                scan[x] = (scan[x] + _paeth(a, b, c)) & 255
        elif filt != 0:
            raise SystemExit(f"unsupported PNG filter {filt}")
        prev = bytes(scan)
        rows.append(prev)
    rgba = bytearray()
    if color_type == 6:
        for row in rows:
            rgba.extend(row)
    else:
        for row in rows:
            for x in range(0, stride, 3):
                rgba.extend(row[x : x + 3])
                rgba.append(255)
    return width, height, rgba


def write_png_rgba(path: Path, width: int, height: int, rgba: bytes) -> None:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def apply_macos_squircle(width: int, height: int, rgba: bytearray) -> bytearray:
    """Cut corners with a macOS app-icon superellipse (n=5) and edge AA."""
    feather = 3.0 / min(width, height)
    out = bytearray(rgba)
    for y in range(height):
        ny = abs((y + 0.5) / height * 2 - 1)
        for x in range(width):
            nx = abs((x + 0.5) / width * 2 - 1)
            value = nx**_SQUIRCLE_N + ny**_SQUIRCLE_N
            if value <= 1 - feather:
                mask = 255
            elif value >= 1 + feather:
                mask = 0
            else:
                t = (value - (1 - feather)) / (2 * feather)
                t = t * t * (3 - 2 * t)
                mask = int(round(255 * (1 - t)))
            i = (y * width + x) * 4 + 3
            out[i] = (out[i] * mask + 127) // 255
    return out


def art_grid_margin_px(size: int) -> int:
    return int(round(size * (_ICON_GRID - _ICON_ART_BOX) / (2 * _ICON_GRID)))


def has_macos_art_grid_margin(width: int, height: int, rgba: bytes) -> bool:
    """True when mid-edge probes in the Apple grid margin are already transparent."""
    if width < 16 or height < 16:
        return False
    margin = art_grid_margin_px(min(width, height))
    if margin < 2:
        return False
    probe = max(1, margin // 2)

    def alpha(x: int, y: int) -> int:
        return rgba[(y * width + x) * 4 + 3]

    return (
        alpha(probe, height // 2) == 0
        and alpha(width - 1 - probe, height // 2) == 0
        and alpha(width // 2, probe) == 0
        and alpha(width // 2, height - 1 - probe) == 0
    )


def fit_macos_icon_grid(width: int, height: int, rgba: bytearray) -> bytearray:
    """Inset artwork to Apple's 824/1024 grid, then apply a squircle to the art box.

    Full-bleed plates look oversized in the Dock next to stock macOS icons.
    """
    art_w = max(1, int(round(width * _ICON_ART_BOX / _ICON_GRID)))
    art_h = max(1, int(round(height * _ICON_ART_BOX / _ICON_GRID)))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.png"
        art = Path(tmp) / "art.png"
        write_png_rgba(src, width, height, bytes(rgba))
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                f"scale={art_w}:{art_h}:flags=lanczos,format=rgba",
                str(art),
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).decode("utf-8", "replace")
            raise SystemExit(f"ffmpeg failed inset scale: {err}")
        aw, ah, art_rgba = read_png_rgba(art)
    masked = apply_macos_squircle(aw, ah, art_rgba)
    out = bytearray(width * height * 4)
    ox = (width - aw) // 2
    oy = (height - ah) // 2
    for y in range(ah):
        src_row = y * aw * 4
        dst_row = ((oy + y) * width + ox) * 4
        out[dst_row : dst_row + aw * 4] = masked[src_row : src_row + aw * 4]
    return out


def _packbits(src: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        if i + 1 < n and src[i] == src[i + 1]:
            j = i + 1
            while j < n and src[j] == src[i] and j - i < 128:
                j += 1
            out.append((257 - (j - i)) & 0xFF)
            out.append(src[i])
            i = j
            continue
        j = i
        while j < n and j - i < 128:
            if j + 1 < n and src[j] == src[j + 1]:
                break
            j += 1
        out.append(j - i - 1)
        out.extend(src[i:j])
        i = j
    return bytes(out)


def _argb_payload(width: int, height: int, rgba: bytes) -> bytes:
    n = width * height
    planes = bytearray(n * 4)
    for i in range(n):
        r, g, b, a = rgba[i * 4 : i * 4 + 4]
        planes[i] = a
        planes[n + i] = r
        planes[2 * n + i] = g
        planes[3 * n + i] = b
    return b"ARGB" + _packbits(bytes(planes))


def _icns_chunk(ostype: bytes, payload: bytes) -> bytes:
    return ostype + struct.pack(">I", 8 + len(payload)) + payload


def _png_at_size(src: Path, dest: Path, size: int) -> bytes:
    # Source is already grid-inset + squircle; only rescale for each ICNS slot.
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            f"scale={size}:{size}:flags=lanczos,format=rgba",
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


def _write_handmade_icns(masked_png: Path, dest: Path) -> None:
    chunks: list[bytes] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for ostype, size in _ICNS_PNG:
            payload = _png_at_size(masked_png, tmp_path / f"{ostype}.png", size)
            chunks.append(_icns_chunk(ostype.encode("ascii"), payload))
        for ostype, size in (("ic04", 16), ("ic05", 32)):
            sized = tmp_path / f"{ostype}.png"
            _png_at_size(masked_png, sized, size)
            w, h, rgba = read_png_rgba(sized)
            chunks.append(_icns_chunk(ostype.encode("ascii"), _argb_payload(w, h, bytes(rgba))))
    body = b"".join(chunks)
    dest.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def main() -> int:
    if not SRC.is_file():
        raise SystemExit(f"Missing {SRC}")
    if shutil.which("ffmpeg") is None:
        raise SystemExit("missing command: ffmpeg")

    width, height, rgba = read_png_rgba(SRC)
    if has_macos_art_grid_margin(width, height, rgba):
        composed = bytearray(rgba)
        print(f"Reusing inset {SRC.name} ({width}x{height})")
    else:
        composed = fit_macos_icon_grid(width, height, rgba)
        write_png_rgba(SRC, width, height, composed)
        print(
            f"Wrote {SRC} ({SRC.stat().st_size} bytes, {width}x{height}, "
            f"art box {_ICON_ART_BOX}/{_ICON_GRID})"
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _png_at_size(SRC, WINDOW_ICON, WINDOW_ICON_SIZE)
        print(f"Wrote {WINDOW_ICON} ({WINDOW_ICON.stat().st_size} bytes)")

        if shutil.which("iconutil") is not None:
            iconset = tmp_path / "app.iconset"
            iconset.mkdir()
            for name, size in _ICONSET_SIZES:
                _png_at_size(SRC, iconset / name, size)
            out = tmp_path / "app.icns"
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
        else:
            _write_handmade_icns(SRC, DEST)

    print(f"Wrote {DEST} ({DEST.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
