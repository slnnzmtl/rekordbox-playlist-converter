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
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
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
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
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
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
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
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_finish_report_mixed_item_results_pass_result_groups(self) -> None:
        """Given converted + failed item_results: When _finish_report runs:
        Then show_done gets Partial title, Converted and Failed siblings, and
        Failed Detail with source → dest."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from convert.models import ItemResult
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(**startup_patches(), report_conversion=None):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                stats = ConvertStats(
                    converted=1,
                    item_results=[
                        ItemResult(
                            source=Path("/music/a.flac"),
                            destination=Path("/tmp/WAV/a.wav"),
                            action="transcode",
                            outcome="succeeded",
                            write="transcode",
                            playlists=("P [WAV]",),
                        ),
                        ItemResult(
                            source=Path("/music/b.flac"),
                            destination=Path("/tmp/WAV/b.wav"),
                            action="transcode",
                            outcome="failed",
                            error="ffmpeg exited 1",
                            playlists=("P [WAV]",),
                        ),
                    ],
                )
                with patch.object(app, "_show_done_dialog") as show_done:
                    app._finish_report(
                        ["P [WAV]: 1 converted, 1 failed"],
                        output="/tmp/out.xml",
                        output_folder=Path("/tmp"),
                        title="Partial",
                        playlists=[("P [WAV]", None)],
                        analytics_stats=stats,
                        playlist_summaries=[
                            ("P [WAV]", "P [WAV]: 1 converted, 1 failed"),
                        ],
                    )
                show_done.assert_called_once()
                kwargs = show_done.call_args.kwargs
                self.assertEqual(kwargs.get("title"), "Partial")
                groups = kwargs.get("result_groups")
                self.assertIsNotNone(groups)
                self.assertEqual(len(groups), 1)
                statuses = {row.status: row for row in groups[0].rows}
                self.assertIn("Converted", statuses)
                self.assertIn("Failed", statuses)
                self.assertIn("→", statuses["Failed"].detail)
                self.assertIn("ffmpeg exited 1", statuses["Failed"].detail)
                guidance = kwargs.get("guidance") or ""
                self.assertIn("Import into Rekordbox", guidance)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_finish_report_mixed_item_results_pass_result_groups(self) -> None:
        """Given converted + failed item_results: When _finish_report runs:
        Then show_done gets Partial title, Converted and Failed siblings, and
        Failed Detail with source → dest."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from convert.models import ItemResult
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(**startup_patches(), report_conversion=None):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                stats = ConvertStats(
                    converted=1,
                    item_results=[
                        ItemResult(
                            source=Path("/music/a.flac"),
                            destination=Path("/tmp/WAV/a.wav"),
                            action="transcode",
                            outcome="succeeded",
                            write="transcode",
                            playlists=("P [WAV]",),
                        ),
                        ItemResult(
                            source=Path("/music/b.flac"),
                            destination=Path("/tmp/WAV/b.wav"),
                            action="transcode",
                            outcome="failed",
                            error="ffmpeg exited 1",
                            playlists=("P [WAV]",),
                        ),
                    ],
                )
                with patch.object(app, "_show_done_dialog") as show_done:
                    app._finish_report(
                        ["P [WAV]: 1 converted, 1 failed"],
                        output="/tmp/out.xml",
                        output_folder=Path("/tmp"),
                        title="Partial",
                        playlists=[("P [WAV]", None)],
                        analytics_stats=stats,
                        playlist_summaries=[
                            ("P [WAV]", "P [WAV]: 1 converted, 1 failed"),
                        ],
                    )
                show_done.assert_called_once()
                kwargs = show_done.call_args.kwargs
                self.assertEqual(kwargs.get("title"), "Partial")
                groups = kwargs.get("result_groups")
                self.assertIsNotNone(groups)
                self.assertEqual(len(groups), 1)
                statuses = {row.status: row for row in groups[0].rows}
                self.assertIn("Converted", statuses)
                self.assertIn("Failed", statuses)
                self.assertIn("→", statuses["Failed"].detail)
                self.assertIn("ffmpeg exited 1", statuses["Failed"].detail)
                guidance = kwargs.get("guidance") or ""
                self.assertIn("Import into Rekordbox", guidance)
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
