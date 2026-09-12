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
from rb_converter_gui import (
    center_over_window_geometry,
    fit_window_geometry,
    progress_action_status_hint,
    total_successful_conversions,
)
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


class CenterOverWindowGeometryTests(unittest.TestCase):
    def test_center_over_window_geometry_centers_dialog_on_parent(self) -> None:
        # Parent not at (0,0); child 500x200 over 1120x720 at +400+240.
        geom = center_over_window_geometry(400, 240, 1120, 720, 500, 200)
        self.assertEqual(geom, "+710+500")


def _list_dialog_text(show_list) -> str:
    """Join the list-of-lines argument from _show_list_dialog mock calls."""
    lines: list[str] = []
    for call in show_list.call_args_list:
        if len(call.args) >= 3 and isinstance(call.args[2], list):
            lines.extend(call.args[2])
    return "\n".join(lines)


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

    def test_cancel_after_encode_errors_surfaces_errors_without_apply_xml(
        self,
    ) -> None:
        """Given convert_unique returns errors and cancel is set: When the GUI
        convert worker finishes cancelled: Then apply_xml is skipped and the
        user still sees the boom encode error (not only Cancelled.)."""
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
        error_text = "boom for x.flac"

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
            ) as showerror, patch.object(
                ConverterApp, "_show_list_dialog", create=True
            ) as show_list:

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
                        return rb.ConvertStats(
                            converted=1, errors=[error_text]
                        )

                    convert_unique.side_effect = convert_and_cancel
                    app._start_convert()
                    root.update_idletasks()
                    for _ in range(20):
                        root.update()

                    apply_xml.assert_not_called()
                    atomic_write.assert_not_called()
                    showerror.assert_not_called()
                    show_list.assert_called()
                    joined = _list_dialog_text(show_list)
                    self.assertIn(
                        "boom",
                        joined,
                        "cancel with known encode errors must surface them in "
                        f"the scrollable list dialog; got lines={joined!r}",
                    )
                    self.assertFalse(app._busy)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_convert_worker_surfaces_encode_errors_after_writing_xml(
        self,
    ) -> None:
        """Given convert_unique returns ConvertStats with errors (not cancelled):
        When the GUI convert worker finishes that playlist: Then it still
        apply_xml + atomic_write_xml, and the user sees the encode error text
        (not a silent clean Done)."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from types import SimpleNamespace
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = SimpleNamespace(
            warnings=[],
            playlist_dir=Path("/tmp"),
            unique=[object()],
            wav_playlist_name="Test [WAV]",
            output_root=object(),
            output=Path("/tmp/out.xml"),
        )
        error_text = "boom for x.flac"

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
                "rb_converter_gui.rb.convert_unique",
                return_value=rb.ConvertStats(
                    converted=1, errors=[error_text]
                ),
            ), patch(
                "rb_converter_gui.rb.apply_xml", return_value=1
            ) as apply_xml, patch(
                "rb_converter_gui.rb.atomic_write_xml"
            ) as atomic_write, patch(
                "rb_converter_gui.messagebox.showerror"
            ) as showerror, patch.object(
                ConverterApp, "_show_list_dialog", create=True
            ) as show_list, patch.object(
                ConverterApp, "_show_done_dialog"
            ) as show_done:

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
                    app._start_convert()
                    root.update_idletasks()
                    for _ in range(20):
                        root.update()

                    apply_xml.assert_called()
                    atomic_write.assert_called()

                    showerror.assert_not_called()
                    show_list.assert_called()
                    joined = _list_dialog_text(show_list)
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
            ), patch(
                "rb_converter_gui.save_preferences"
            ), patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch(
                "rb_converter_gui.rb.convert_unique"
            ) as convert_unique, patch(
                "rb_converter_gui.rb.apply_xml"
            ) as apply_xml, patch(
                "rb_converter_gui.messagebox.showerror"
            ) as showerror:

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
                    return None, []

                def run_inline(target=None, **_kwargs):
                    class _T:
                        def start(self_inner):
                            target()

                    return _T()

                with patch(
                    "rb_converter_gui.rb.prepare", side_effect=prepare_then_cancel
                ), patch("rb_converter_gui.threading.Thread", side_effect=run_inline):
                    root = tk.Tk()
                    root.withdraw()
                    app = ConverterApp(root, documents_accessible=False)
                    app.xml_var.set("/tmp/test.xml")
                    _seed_track_selection(app)
                    app._start_convert()
                    root.update_idletasks()
                    for _ in range(20):
                        root.update()

                    convert_unique.assert_not_called()
                    apply_xml.assert_not_called()
                    showerror.assert_not_called()
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
        """Given convert_unique + apply_xml + atomic_write_xml succeed: When
        cancel_event is set before finish scheduling: Then the GUI takes the
        _finish_cancelled path (status Cancelled.), not _finish_ok / Done."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from types import SimpleNamespace
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = SimpleNamespace(
            warnings=[],
            playlist_dir=Path("/tmp"),
            unique=[object()],
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
                "rb_converter_gui.rb.convert_unique",
                return_value=rb.ConvertStats(converted=1),
            ), patch(
                "rb_converter_gui.rb.apply_xml", return_value=1
            ) as apply_xml, patch(
                "rb_converter_gui.rb.atomic_write_xml"
            ) as atomic_write, patch(
                "rb_converter_gui.messagebox.showerror"
            ) as showerror, patch.object(
                ConverterApp, "_show_done_dialog"
            ) as show_done:

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

                    def atomic_then_cancel(*_a, **_k):
                        app._cancel_event.set()

                    atomic_write.side_effect = atomic_then_cancel
                    app._start_convert()
                    root.update_idletasks()
                    for _ in range(20):
                        root.update()

                    apply_xml.assert_called()
                    atomic_write.assert_called()
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


class ProgressStatusHintTests(unittest.TestCase):
    def test_progress_action_status_hint_puts_counter_after_action(self) -> None:
        """Hint reads Convert (n/m) TrackName… so the counter stays visible."""
        self.assertEqual(
            progress_action_status_hint(
                "convert",
                357,
                1958,
                "0190 - Posij - Sun Tracker.wav",
            ),
            "Convert (357/1958) 0190 - Posij - Sun Tracker.wav…",
        )


if __name__ == "__main__":
    unittest.main()
