#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_SRC, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from convert.models import ConvertStats
from gui_tk import app_patches, startup_patches, tk_available


class GuiAnalyticsTests(unittest.TestCase):
    def test_analytics_checkbutton_enable_calls_enable_analytics(self) -> None:
        """Given analytics off: When the Help checkbutton turns on: Then
        enable_analytics(surface=gui) is called."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(),
                enable_analytics=None,
                disable_analytics=None,
            ) as mocks:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                self.assertFalse(app.analytics_var.get())
                app.analytics_var.set(True)
                app._on_analytics_toggle()
                mocks["enable_analytics"].assert_called_once_with(surface="gui")
                mocks["disable_analytics"].assert_not_called()
        finally:
            if root is not None:
                root.destroy()

    def test_analytics_checkbutton_disable_calls_disable_analytics(self) -> None:
        """Given analytics on in prefs: When the checkbutton turns off: Then
        disable_analytics is called."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(preferences={"analytics": "on"}),
                enable_analytics=None,
                disable_analytics=None,
            ) as mocks:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                self.assertTrue(app.analytics_var.get())
                app.analytics_var.set(False)
                app._on_analytics_toggle()
                mocks["disable_analytics"].assert_called_once_with()
                mocks["enable_analytics"].assert_not_called()
        finally:
            if root is not None:
                root.destroy()

    def test_finish_report_done_reports_conversion(self) -> None:
        """Given title Done: When _finish_report runs: Then report_conversion
        is called with surface gui."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(),
                report_conversion=None,
            ) as mocks:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                stats = ConvertStats(converted=1, appended=1)
                with patch.object(app, "_show_done_dialog"):
                    app._finish_report(
                        ["1 converted"],
                        output="/tmp/out.xml",
                        output_folder=Path("/tmp"),
                        title="Done",
                        analytics_stats=stats,
                        analytics_source_root=None,
                    )
                mocks["report_conversion"].assert_called_once()
                kwargs = mocks["report_conversion"].call_args.kwargs
                self.assertEqual(kwargs["surface"], "gui")
                self.assertIs(kwargs["stats"], stats)
        finally:
            if root is not None:
                root.destroy()

    def test_finish_report_partial_does_not_report_conversion(self) -> None:
        """Given title Partial: When _finish_report runs: Then report_conversion
        is not called."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(),
                report_conversion=None,
            ) as mocks:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                with patch.object(app, "_show_done_dialog"):
                    app._finish_report(
                        ["partial"],
                        output="/tmp/out.xml",
                        output_folder=Path("/tmp"),
                        title="Partial",
                        analytics_stats=ConvertStats(converted=1),
                    )
                mocks["report_conversion"].assert_not_called()
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
