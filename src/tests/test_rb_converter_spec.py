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
    def test_spec_targets_universal2(self) -> None:
        self.assertIn('target_arch="universal2"', _SPEC)

    def test_bundle_identifier_is_not_rekordbox_tld(self) -> None:
        self.assertIn(
            'bundle_identifier="io.github.slnnzmtl.rekordboxWavConverter"',
            _SPEC,
        )
        self.assertNotIn('bundle_identifier="com.rekordbox.', _SPEC)

    def test_spec_always_bundles_licenses(self) -> None:
        self.assertIn('project_license = root / "LICENSE"', _SPEC)
        self.assertIn("COPYING.GPLv3", _SPEC)
        self.assertIn('datas.append((str(project_license), "."))', _SPEC)

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
    def test_bundle_uses_committed_icns_icon(self) -> None:
        self.assertIn('app_icns = root / "assets" / "app.icns"', _SPEC)
        self.assertIn("icon=str(app_icns)", _SPEC)
        self.assertNotIn("icon=None", _SPEC)
        icon = _REPO / "assets" / "app.icns"
        self.assertTrue(icon.is_file(), "assets/app.icns must exist for the macOS app icon")
        self.assertTrue(icon.read_bytes().startswith(b"icns"))

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

    def test_spec_bundles_logo_png(self) -> None:
        self.assertIn("rpc-logo-white.png", _SPEC)
        logo = _REPO / "assets" / "rpc-logo-white.png"
        self.assertTrue(logo.is_file(), "assets/rpc-logo-white.png must exist")
        self.assertTrue(logo.read_bytes().startswith(b"\x89PNG"))

    def test_spec_bundles_256px_window_icon(self) -> None:
        self.assertIn("rpc-logo-white-256.png", _SPEC)
        icon = _REPO / "assets" / "rpc-logo-white-256.png"
        self.assertTrue(icon.is_file(), "assets/rpc-logo-white-256.png must exist")
        self.assertTrue(icon.read_bytes().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
