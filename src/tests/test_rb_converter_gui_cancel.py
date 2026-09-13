#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import rb_playlist_to_wav as rb
from cli_error import CancelledError, CliError
import converter_manifest

from gui_tk import (
    app_patches,
    empty_conversion_preview,
    find_listbox,
    list_dialog_text,
    mark_output_folder_valid,
    merge_patches,
    mock_convert_plan,
    mock_prepared_conversion,
    run_inline_thread,
    seed_track_selection,
    start_convert_and_confirm,
    startup_patches,
    tk_available,
)

class ProgressBusyVisibilityTests(unittest.TestCase):
    def test_finish_cancelled_clears_busy_without_error_dialog(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {"messagebox.showerror": None},
                )
            ) as mocks:
                showerror = mocks["messagebox.showerror"]
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(**startup_patches()):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                import xml.etree.ElementTree as ET
                from gui.types import PlaylistEntry, PlaylistNodeKind

                app._playlist_entries = [
                    PlaylistEntry(
                        PlaylistNodeKind.PLAYLIST, "ROOT", "A", 1, ET.Element("x")
                    ),
                    PlaylistEntry(
                        PlaylistNodeKind.PLAYLIST, "ROOT", "B", 2, ET.Element("y")
                    ),
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

    def test_cancel_after_encode_writes_import_xml_then_finishes_cancelled(
        self,
    ) -> None:
        """Given execute_prepared returns successes (and optional encode errors)
        then sets cancel: When the GUI write worker finishes: Then status is
        Cancelled and encode errors still surface in the list dialog."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        plan = mock_convert_plan(n_unique=2)
        error_text = "boom for x.flac"
        succeeded = {("sk", "wav")}

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"return_value": (mock_prepared_conversion(n_unique=2), [])},
                        "execute_prepared": {"create": True},
                        "messagebox.showerror": None,
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ) as mocks, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch.object(
                ConverterApp, "_show_conversion_preview"
            ), patch.object(
                ConverterApp, "_show_list_dialog", create=True
            ) as show_list:
                execute = mocks["execute_prepared"]
                showerror = mocks["messagebox.showerror"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)

                def execute_and_cancel(*_args, **_kwargs):
                    app._cancel_event.set()
                    return rb.ConvertStats(
                        converted=1,
                        errors=[error_text],
                        succeeded=succeeded,
                        appended_by_plan=[1],
                    )

                execute.side_effect = execute_and_cancel
                mark_output_folder_valid(app)
                start_convert_and_confirm(app, root)

                execute.assert_called()
                showerror.assert_not_called()
                show_list.assert_called()
                joined = list_dialog_text(show_list)
                self.assertIn(
                    "boom",
                    joined,
                    "cancel with known encode errors must surface them in "
                    f"the scrollable list dialog; got lines={joined!r}",
                )
                self.assertEqual(app.status_var.get(), "Cancelled.")
                self.assertFalse(app._busy)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_convert_worker_surfaces_encode_errors_after_writing_xml(
        self,
    ) -> None:
        """Given execute_prepared returns ConvertStats with errors (not cancelled):
        When the GUI write worker finishes: Then the user sees the encode error
        text (not a silent clean Done)."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        plan = mock_convert_plan(n_unique=1)
        error_text = "boom for x.flac"

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"return_value": (mock_prepared_conversion(n_unique=2), [])},
                        "execute_prepared": {
                            "create": True,
                            "return_value": rb.ConvertStats(
                                converted=1,
                                errors=[error_text],
                                appended_by_plan=[1],
                            ),
                        },
                        "messagebox.showerror": None,
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ) as mocks, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch.object(
                ConverterApp, "_show_conversion_preview"
            ), patch.object(
                ConverterApp, "_show_list_dialog", create=True
            ) as show_list, patch.object(
                ConverterApp, "_show_done_dialog"
            ) as show_done:
                execute = mocks["execute_prepared"]
                showerror = mocks["messagebox.showerror"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                start_convert_and_confirm(app, root)

                execute.assert_called()

                showerror.assert_not_called()
                show_list.assert_called()
                joined = list_dialog_text(show_list)
                self.assertIn(
                    "boom",
                    joined,
                    "encode errors must appear in the scrollable list "
                    f"dialog; got lines={joined!r}",
                )
                self.assertFalse(
                    show_done.called,
                    "must not finish as a clean Done when encode errors exist",
                )
                self.assertFalse(app._busy)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_cancel_during_prepare_finishes_cancelled(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:

            def prepare_then_cancel(
                *_args,
                cancel_event=None,
                on_progress=None,
                **_kwargs,
            ):
                self.assertIsNotNone(cancel_event)
                self.assertIsNotNone(on_progress)
                on_progress(1, 2, "prepare", "track.wav")
                cancel_event.set()
                raise CancelledError("conversion cancelled")

            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"side_effect": prepare_then_cancel},
                        "execute_prepared": {"create": True},
                        "messagebox.showerror": None,
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ) as mocks, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ):
                execute = mocks["execute_prepared"]
                showerror = mocks["messagebox.showerror"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                app._start_convert()
                root.update_idletasks()
                for _ in range(20):
                    root.update()

                execute.assert_not_called()
                showerror.assert_not_called()
                self.assertEqual(app.status_var.get(), "Cancelled.")
                self.assertFalse(app._busy)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_cancel_during_preview_classify_finishes_cancelled(self) -> None:
        """Given prepare succeeds: When build_conversion_preview raises
        CancelledError: Then the GUI finishes cancelled (not an error dialog)."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        plan = mock_convert_plan(n_unique=2)
        root = None
        try:
            progress_actions: list[str] = []

            def preview_then_cancel(
                *_args,
                cancel_event=None,
                on_progress=None,
                **_kwargs,
            ):
                self.assertIsNotNone(cancel_event)
                self.assertIsNotNone(on_progress)
                on_progress(1, 2, "preview", "a.wav")
                progress_actions.append("preview")
                cancel_event.set()
                raise CancelledError("conversion cancelled during preview")

            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"side_effect": preview_then_cancel},
                        "execute_prepared": {"create": True},
                        "messagebox.showerror": None,
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ) as mocks, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ):
                execute = mocks["execute_prepared"]
                showerror = mocks["messagebox.showerror"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                app._start_convert()
                root.update_idletasks()
                for _ in range(20):
                    root.update()

                execute.assert_not_called()
                showerror.assert_not_called()
                self.assertEqual(progress_actions, ["preview"])
                self.assertEqual(app.status_var.get(), "Cancelled.")
                self.assertFalse(app._busy)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_late_cancel_after_atomic_write_finishes_cancelled_not_done(
        self,
    ) -> None:
        """Given execute_prepared succeeds: When cancel_event is set before
        finish scheduling: Then the GUI takes the _finish_cancelled path
        (status Cancelled.), not _finish_ok / Done."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        plan = mock_convert_plan(n_unique=1)

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {
                            "return_value": (mock_prepared_conversion(n_unique=1), [])
                        },
                        "execute_prepared": {"create": True},
                        "messagebox.showerror": None,
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ) as mocks, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch.object(
                ConverterApp, "_show_conversion_preview"
            ), patch.object(
                ConverterApp, "_show_done_dialog"
            ) as show_done:
                execute = mocks["execute_prepared"]
                showerror = mocks["messagebox.showerror"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)

                def execute_then_cancel(*_a, **_k):
                    app._cancel_event.set()
                    return rb.ConvertStats(converted=1, appended_by_plan=[1])

                execute.side_effect = execute_then_cancel
                mark_output_folder_valid(app)
                start_convert_and_confirm(app, root)

                execute.assert_called()
                showerror.assert_not_called()
                self.assertEqual(
                    app.status_var.get(),
                    "Cancelled.",
                    "late cancel after XML write must finish cancelled, "
                    f"not Done; got {app.status_var.get()!r}",
                )
                show_done.assert_not_called()
                self.assertFalse(app._busy)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

class MissingFilesDialogTests(unittest.TestCase):
    def test_finish_no_conversions_lists_missing_paths_in_scrollbox(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        warnings = [
            "missing source file: /Volumes/SSD/a.flac",
            "missing source file: /Volumes/SSD/b.flac",
        ]
        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {"messagebox.showwarning": None},
                )
            ), patch.object(tk.Toplevel, "wait_window"):
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
                listbox = find_listbox(dlg)
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

if __name__ == "__main__":
    unittest.main()
