#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from update_check import UpdateCheckResult
from version import __version__


class VersionDisplayTests(unittest.TestCase):
    def test_version_matches_current_release(self) -> None:
        self.assertEqual(__version__, "2.0.0")

    def test_spec_bundle_version_matches_app_version(self) -> None:
        spec = (_REPO / "rb_converter.spec").read_text()
        self.assertIn(f'"CFBundleShortVersionString": "{__version__}"', spec)
        self.assertIn(f'"CFBundleVersion": "{__version__}"', spec)

    def test_gui_shows_version_in_window_title(self) -> None:
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
                ConverterApp(root, documents_accessible=False)
            self.assertEqual(
                root.title(),
                f"Simple Rekordbox Converter {__version__}",
            )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
