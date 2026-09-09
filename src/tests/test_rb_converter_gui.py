#!/usr/bin/env python3
from __future__ import annotations

import struct
import sys
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import rb_playlist_to_wav as rb
from rb_converter_gui import total_successful_conversions
from update_check import UpdateCheckResult


def _png_rgba(path: Path) -> tuple[int, int, bytes]:
    """Decode an 8-bit RGB/RGBA PNG to tightly packed RGBA bytes."""
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"{path} is not a PNG")
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
        raise ValueError(f"{path} is not an 8-bit RGB/RGBA PNG")
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
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                scan[x] = (scan[x] + pr) & 255
        elif filt != 0:
            raise ValueError(f"unsupported PNG filter {filt}")
        prev = bytes(scan)
        rows.append(prev)
    if color_type == 6:
        return width, height, b"".join(rows)
    rgba = bytearray()
    for row in rows:
        for x in range(0, stride, 3):
            rgba.extend(row[x : x + 3])
            rgba.append(255)
    return width, height, bytes(rgba)


class TotalSuccessfulConversionsTests(unittest.TestCase):
    def test_total_successful_conversions_counts_converted(self) -> None:
        self.assertEqual(total_successful_conversions([rb.ConvertStats(converted=3)]), 3)

    def test_total_successful_conversions_counts_copied(self) -> None:
        self.assertEqual(total_successful_conversions([rb.ConvertStats(copied=2)]), 2)

    def test_total_successful_conversions_sums_converted_and_copied(self) -> None:
        self.assertEqual(
            total_successful_conversions([rb.ConvertStats(converted=1, copied=2)]), 3
        )

    def test_total_successful_conversions_ignores_skipped_and_appended(self) -> None:
        self.assertEqual(
            total_successful_conversions(
                [rb.ConvertStats(converted=0, copied=0, skipped=5, appended=10)]
            ),
            0,
        )

    def test_total_successful_conversions_sums_across_multiple_stats(self) -> None:
        self.assertEqual(
            total_successful_conversions(
                [
                    rb.ConvertStats(converted=1),
                    rb.ConvertStats(copied=2),
                    rb.ConvertStats(skipped=9),
                ]
            ),
            3,
        )

    def test_total_successful_conversions_empty_list(self) -> None:
        self.assertEqual(total_successful_conversions([]), 0)


class AppLogoTests(unittest.TestCase):
    def test_app_logo_png_is_committed(self) -> None:
        from rb_converter_gui import app_logo_path

        logo = Path(__file__).resolve().parents[2] / "assets" / "rpc-logo-white.png"
        self.assertTrue(logo.is_file())
        self.assertTrue(logo.read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(app_logo_path().resolve(), logo)

    def test_window_icon_png_is_committed_256px(self) -> None:
        from rb_converter_gui import app_window_icon_path

        icon = Path(__file__).resolve().parents[2] / "assets" / "rpc-logo-white-256.png"
        self.assertTrue(icon.is_file())
        self.assertTrue(icon.read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(app_window_icon_path().resolve(), icon)

    def test_window_icon_has_macos_rounded_corners(self) -> None:
        icon = Path(__file__).resolve().parents[2] / "assets" / "rpc-logo-white-256.png"
        width, height, rgba = _png_rgba(icon)
        self.assertEqual((width, height), (256, 256))

        def alpha(x: int, y: int) -> int:
            return rgba[(y * width + x) * 4 + 3]

        self.assertEqual(alpha(0, 0), 0)
        self.assertEqual(alpha(width - 1, 0), 0)
        self.assertEqual(alpha(0, height - 1), 0)
        self.assertEqual(alpha(width - 1, height - 1), 0)
        self.assertEqual(alpha(width // 2, height // 2), 255)

    def test_window_icon_follows_macos_art_grid_inset(self) -> None:
        """Dock-sized icons use Apple's 824/1024 art box (~100px margin @1024).

        Full-bleed squircles read oversized next to stock macOS Dock icons.
        """
        icon = Path(__file__).resolve().parents[2] / "assets" / "rpc-logo-white-256.png"
        width, height, rgba = _png_rgba(icon)
        self.assertEqual((width, height), (256, 256))

        def alpha(x: int, y: int) -> int:
            return rgba[(y * width + x) * 4 + 3]

        # Margin is (1024-824)/(2*1024) of the canvas; probe halfway into it.
        margin = int(round(width * (1024 - 824) / (2 * 1024)))
        self.assertGreater(margin, 4)
        probe = margin // 2
        self.assertEqual(alpha(probe, height // 2), 0)
        self.assertEqual(alpha(width - 1 - probe, height // 2), 0)
        self.assertEqual(alpha(width // 2, probe), 0)
        self.assertEqual(alpha(width // 2, height - 1 - probe), 0)

    def test_source_logo_has_macos_rounded_corners(self) -> None:
        logo = Path(__file__).resolve().parents[2] / "assets" / "rpc-logo-white.png"
        width, height, rgba = _png_rgba(logo)
        self.assertEqual(width, height)

        def alpha(x: int, y: int) -> int:
            return rgba[(y * width + x) * 4 + 3]

        self.assertEqual(alpha(0, 0), 0)
        self.assertEqual(alpha(width - 1, height - 1), 0)
        self.assertEqual(alpha(width // 2, height // 2), 255)

    def test_source_logo_follows_macos_art_grid_inset(self) -> None:
        logo = Path(__file__).resolve().parents[2] / "assets" / "rpc-logo-white.png"
        width, height, rgba = _png_rgba(logo)
        margin = int(round(width * (1024 - 824) / (2 * 1024)))
        probe = margin // 2

        def alpha(x: int, y: int) -> int:
            return rgba[(y * width + x) * 4 + 3]

        self.assertEqual(alpha(probe, height // 2), 0)
        self.assertEqual(alpha(width // 2, probe), 0)

    def test_gui_applies_app_logo_as_window_icon(self) -> None:
        try:
            import _tkinter  # noqa: F401
        except ImportError:
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
            logo = getattr(app, "logo_image", None)
            self.assertIsNotNone(logo)
            self.assertEqual(int(logo.width()), 256)
            self.assertEqual(int(logo.height()), 256)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
