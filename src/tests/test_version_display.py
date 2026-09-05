#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from version import __version__


class VersionDisplayTests(unittest.TestCase):
    def test_version_matches_current_release(self) -> None:
        self.assertEqual(__version__, "1.0.0")

    def test_spec_bundle_version_matches_app_version(self) -> None:
        spec = (_REPO / "rb_converter.spec").read_text()
        self.assertIn(f'"CFBundleShortVersionString": "{__version__}"', spec)

    def test_gui_shows_version_below_title(self) -> None:
        try:
            import _tkinter  # noqa: F401
        except ImportError:
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            app = ConverterApp(root)
            self.assertEqual(app.title_label.cget("text"), "Rekordbox WAV Converter")
            self.assertEqual(app.version_label.cget("text"), __version__)
            title_row = int(app.title_label.grid_info()["row"])
            version_row = int(app.version_label.grid_info()["row"])
            self.assertGreater(version_row, title_row)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
