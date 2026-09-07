#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import rb_playlist_to_wav as rb
from rb_converter_gui import total_successful_conversions
from update_check import UpdateCheckResult


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
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
            logo = getattr(app, "logo_image", None)
            self.assertIsNotNone(logo)
            self.assertGreater(int(logo.width()), 0)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
