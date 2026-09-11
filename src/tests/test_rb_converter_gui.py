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


def _tk_available() -> bool:
    try:
        import _tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def _find_listbox(widget):
    import tkinter as tk

    if isinstance(widget, tk.Listbox):
        return widget
    for child in widget.winfo_children():
        found = _find_listbox(child)
        if found is not None:
            return found
    return None


def _find_scrollbar(widget):
    import tkinter as tk
    from tkinter import ttk

    if isinstance(widget, (tk.Scrollbar, ttk.Scrollbar)):
        return widget
    for child in widget.winfo_children():
        found = _find_scrollbar(child)
        if found is not None:
            return found
    return None


class TotalSuccessfulConversionsTests(unittest.TestCase):
    def test_total_successful_conversions(self) -> None:
        self.assertEqual(
            total_successful_conversions(
                [
                    rb.ConvertStats(converted=1, copied=2),
                    rb.ConvertStats(skipped=9, appended=10),
                ]
            ),
            3,
        )
        self.assertEqual(
            total_successful_conversions(
                [rb.ConvertStats(converted=0, copied=0, skipped=5, appended=10)]
            ),
            0,
        )
        self.assertEqual(total_successful_conversions([]), 0)


class MissingFilesDialogTests(unittest.TestCase):
    def test_finish_no_conversions_lists_missing_paths_in_scrollbox(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        warnings = [
            "missing source file: /Volumes/SSD/a.flac",
            "missing source file: /Volumes/SSD/b.flac",
        ]
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch(
                "rb_converter_gui.load_preferences",
                return_value={},
            ), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch(
                "rb_converter_gui.rb.discover_xml_candidates", return_value=[]
            ), patch.object(tk.Toplevel, "wait_window"), patch(
                "rb_converter_gui.messagebox.showwarning"
            ) as showwarning:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._finish_no_conversions(
                    ["48khz [WAV]: 2 missing skipped"],
                    warnings,
                )
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                listbox = _find_listbox(dlg)
                self.assertIsNotNone(listbox)
                self.assertEqual(
                    list(listbox.get(0, tk.END)),
                    warnings,
                )
                self.assertIsNotNone(_find_scrollbar(dlg))
                showwarning.assert_not_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
