#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[2]
_SPEC = (_REPO / "rb_converter.spec").read_text()


def _load_require_universal2():
    preamble = _SPEC.split("binaries = []", 1)[0]
    ns: dict = {"SPECPATH": str(_REPO)}
    exec(preamble, ns)
    return ns["require_universal2"]


class SpecUniversal2Tests(unittest.TestCase):
    def test_require_universal2_accepts_fat_binary(self) -> None:
        require = _load_require_universal2()
        result = subprocess.CompletedProcess(
            ["lipo", "-archs", "ffmpeg"], 0, stdout="x86_64 arm64\n", stderr=""
        )
        with patch("subprocess.run", return_value=result) as run:
            require(Path("vendor/ffmpeg/ffmpeg"))
        run.assert_called_once()

    def test_require_universal2_rejects_thin_binary(self) -> None:
        require = _load_require_universal2()
        result = subprocess.CompletedProcess(
            ["lipo", "-archs", "ffmpeg"], 0, stdout="arm64\n", stderr=""
        )
        with patch("subprocess.run", return_value=result):
            with self.assertRaises(SystemExit) as ctx:
                require(Path("vendor/ffmpeg/ffmpeg"))
        self.assertIn("not universal2", str(ctx.exception))

    def test_require_universal2_requires_lipo(self) -> None:
        require = _load_require_universal2()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(SystemExit) as ctx:
                require(Path("vendor/ffmpeg/ffmpeg"))
        self.assertIn("lipo not found", str(ctx.exception))


def _icns_chunk_payloads(data: bytes) -> dict[bytes, bytes]:
    if not data.startswith(b"icns") or len(data) < 8:
        return {}
    declared = int.from_bytes(data[4:8], "big")
    end = min(declared, len(data))
    payloads: dict[bytes, bytes] = {}
    i = 8
    while i + 8 <= end:
        ostype = data[i : i + 4]
        size = int.from_bytes(data[i + 4 : i + 8], "big")
        if size < 8 or i + size > end:
            break
        payloads[ostype] = data[i + 8 : i + size]
        i += size
    return payloads


class SpecAppIconTests(unittest.TestCase):
    def test_app_icns_includes_finder_list_view_argb_icons(self) -> None:
        """Finder list/column views need legacy ARGB icons (ic04/ic05).

        PNG-only handmade .icns files often render as colorful static there.
        """
        icon = _REPO / "assets" / "app.icns"
        payloads = _icns_chunk_payloads(icon.read_bytes())
        types = sorted(payloads)
        self.assertTrue(
            b"ic04" in payloads and payloads[b"ic04"].startswith(b"ARGB"),
            f"missing 16px ARGB icon (ic04); found {types}",
        )
        self.assertTrue(
            b"ic05" in payloads and payloads[b"ic05"].startswith(b"ARGB"),
            f"missing 32px ARGB icon (ic05); found {types}",
        )


if __name__ == "__main__":
    unittest.main()
