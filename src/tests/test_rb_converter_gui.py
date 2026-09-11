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
from rb_converter_gui import fit_window_geometry, total_successful_conversions
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


def _seed_track_selection(app, folder="ROOT", name="Test", key="1"):
    """Paint one selected leaf so Convert can proceed without loading a real XML."""
    import tkinter as tk

    leaf = app.tracklist_tree.insert("", tk.END, text="seed")
    app._tracklist_iids[leaf] = (folder, name, key)
    app.tracklist_tree.selection_set(leaf)
    return leaf


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


class FitWindowGeometryTests(unittest.TestCase):
    def test_fit_window_geometry_centers_inside_nonzero_origin_rect(self) -> None:
        # Secondary-display-style rect: not at virtual (0,0).
        # 1440x900 usable; 1120x720 centered at +2080+115.
        geom = fit_window_geometry(1120, 720, 1920, 25, 3360, 925)
        self.assertEqual(geom, "1120x720+2080+115")


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
            ):
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
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


class ProgressBusyVisibilityTests(unittest.TestCase):
    def test_finish_cancelled_clears_busy_without_error_dialog(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

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
            ), patch("rb_converter_gui.messagebox.showerror") as showerror:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._set_busy(True)
                app._finish_cancelled()
                self.assertFalse(app._busy)
                self.assertEqual(app.status_var.get(), "Cancelled.")
                showerror.assert_not_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_cancelled_status_clears_after_three_seconds(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

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
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._playlist_entries = [
                    ("playlist", "ROOT", "A", 1, object()),
                    ("playlist", "ROOT", "B", 2, object()),
                ]
                scheduled: list[tuple[int, object]] = []
                real_after = root.after

                def capture_after(ms, func=None, *args):
                    if func is None:
                        return real_after(ms)
                    scheduled.append((ms, func if not args else lambda: func(*args)))
                    return "after-id"

                with patch.object(root, "after", side_effect=capture_after):
                    app._set_busy(True)
                    app.progress["value"] = 42
                    app._finish_cancelled()

                self.assertEqual(app.status_var.get(), "Cancelled.")
                clears = [fn for ms, fn in scheduled if ms == 3000]
                self.assertEqual(len(clears), 1)
                clears[0]()
                self.assertEqual(
                    app.status_var.get(),
                    "Loaded 2 playlist(s). Select and Convert.",
                )
                self.assertEqual(float(app.progress["value"]), 0.0)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_cancel_skips_apply_xml_for_interrupted_playlist(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from types import SimpleNamespace
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = SimpleNamespace(
            warnings=[],
            playlist_dir=Path("/tmp"),
            unique=[object(), object()],
            wav_playlist_name="Test [WAV]",
            output_root=object(),
            output=Path("/tmp/out.xml"),
        )

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
            ), patch(
                "rb_converter_gui.save_preferences"
            ), patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch(
                "rb_converter_gui.rb.prepare", return_value=(plan, [])
            ), patch(
                "rb_converter_gui.rb.share_output_root"
            ), patch(
                "rb_converter_gui.rb.convert_unique"
            ) as convert_unique, patch(
                "rb_converter_gui.rb.apply_xml"
            ) as apply_xml, patch(
                "rb_converter_gui.rb.atomic_write_xml"
            ) as atomic_write, patch(
                "rb_converter_gui.messagebox.showerror"
            ) as showerror:

                def run_inline(target=None, **_kwargs):
                    class _T:
                        def start(self_inner):
                            target()

                    return _T()

                with patch("rb_converter_gui.threading.Thread", side_effect=run_inline):
                    root = tk.Tk()
                    root.withdraw()
                    app = ConverterApp(root, documents_accessible=False)
                    app.xml_var.set("/tmp/test.xml")
                    _seed_track_selection(app)

                    def convert_and_cancel(*_args, **_kwargs):
                        app._cancel_event.set()
                        return rb.ConvertStats(converted=1)

                    convert_unique.side_effect = convert_and_cancel
                    app._start_convert()
                    root.update_idletasks()
                    for _ in range(20):
                        root.update()

                    apply_xml.assert_not_called()
                    atomic_write.assert_not_called()
                    showerror.assert_not_called()
                    self.assertEqual(app.status_var.get(), "Cancelled.")
                    self.assertFalse(app._busy)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
