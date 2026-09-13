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
import converter_manifest
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


def _mark_output_folder_valid(app) -> None:
    after_id = getattr(app, "_wav_dir_validate_after_id", None)
    if after_id is not None:
        app.root.after_cancel(after_id)
        app._wav_dir_validate_after_id = None
    app._wav_dir_checking = False
    app._wav_dir_valid = True
    app._set_wav_dir_error("")
    app._update_convert_enabled()


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


def _confirm_conversion_preview(app) -> None:
    """Confirm the in-memory prepared payload (preview Convert)."""
    app._confirm_prepared_conversion()


def _pump_ui(root, times: int = 20) -> None:
    root.update_idletasks()
    for _ in range(times):
        root.update()


def _start_convert_and_confirm(app, root) -> None:
    """Prepare then confirm, matching the post-preview write path."""
    app._start_convert()
    _pump_ui(root)
    _confirm_conversion_preview(app)
    _pump_ui(root)


def _mock_convert_plan(*, n_unique: int = 2):
    """Minimal Plan-like object for GUI convert-worker tests (batch unique path)."""
    from types import SimpleNamespace

    unique = [
        SimpleNamespace(
            source_path=Path(f"/tmp/track{i}.flac"),
            dest_name=f"track{i}.wav",
            dest_path=Path(f"/tmp/WAV/track{i}.wav"),
            bit_depth=24,
            sample_rate=48000,
            duration_seconds=1.0,
            noop=False,
            copy_wav=False,
        )
        for i in range(n_unique)
    ]
    return SimpleNamespace(
        warnings=[],
        tracks=list(unique),
        playlist_dir=Path("/tmp/WAV"),
        unique=unique,
        wav_playlist_name="Test [WAV]",
        wav_dir=Path("/tmp"),
        output_root=object(),
        output=Path("/tmp/out.xml"),
        output_format="wav",
        cover_cache={},
    )


def _empty_conversion_preview(*, selected: int = 0) -> rb.ConversionPreview:
    """Minimal preview for GUI tests that stub threading.Thread (pool-hostile)."""
    return rb.ConversionPreview(
        selected=selected,
        resolved=selected,
        unique_outputs=selected,
        duplicates=0,
        missing=0,
        items=[],
    )


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
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = _mock_convert_plan(n_unique=2)

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
                "rb_converter_gui.converter_manifest.save_manifest"
            ), patch(
                "rb_converter_gui.rb.build_conversion_preview",
                return_value=_empty_conversion_preview(selected=2),
            ), patch.object(
                ConverterApp, "_show_conversion_preview"
            ), patch(
                "rb_converter_gui.rb.convert_unique"
            ) as convert_unique, patch(
                "rb_converter_gui.rb.apply_xml"
            ) as apply_xml, patch(
                "rb_converter_gui.rb.write_import_xml"
            ) as write_xml, patch(
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
                    _mark_output_folder_valid(app)
                    _start_convert_and_confirm(app, root)

                    apply_xml.assert_not_called()
                    write_xml.assert_not_called()
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
        convert worker finishes cancelled: Then no completed playlist is written
        and the user still sees the boom encode error (not only Cancelled.)."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = _mock_convert_plan(n_unique=2)
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
                "rb_converter_gui.converter_manifest.save_manifest"
            ), patch(
                "rb_converter_gui.rb.build_conversion_preview",
                return_value=_empty_conversion_preview(selected=2),
            ), patch.object(
                ConverterApp, "_show_conversion_preview"
            ), patch(
                "rb_converter_gui.rb.convert_unique"
            ) as convert_unique, patch(
                "rb_converter_gui.rb.apply_xml"
            ) as apply_xml, patch(
                "rb_converter_gui.rb.write_import_xml"
            ) as write_xml, patch(
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
                    _mark_output_folder_valid(app)
                    _start_convert_and_confirm(app, root)

                    apply_xml.assert_not_called()
                    write_xml.assert_not_called()
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
        apply_xml + write_import_xml, and the user sees the encode error text
        (not a silent clean Done)."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = _mock_convert_plan(n_unique=1)
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
                "rb_converter_gui.converter_manifest.save_manifest"
            ), patch(
                "rb_converter_gui.rb.build_conversion_preview",
                return_value=_empty_conversion_preview(selected=2),
            ), patch.object(
                ConverterApp, "_show_conversion_preview"
            ), patch(
                "rb_converter_gui.rb.convert_unique",
                return_value=rb.ConvertStats(
                    converted=1, errors=[error_text]
                ),
            ), patch(
                "rb_converter_gui.rb.apply_xml", return_value=1
            ) as apply_xml, patch(
                "rb_converter_gui.rb.write_import_xml"
            ) as write_xml, patch(
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
                    _mark_output_folder_valid(app)
                    _start_convert_and_confirm(app, root)

                    apply_xml.assert_called()
                    write_xml.assert_called()

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
                    _mark_output_folder_valid(app)
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

    def test_cancel_during_preview_classify_finishes_cancelled(self) -> None:
        """Given prepare succeeds: When build_conversion_preview raises
        CancelledError: Then the GUI finishes cancelled (not an error dialog)."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = _mock_convert_plan(n_unique=2)
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
            ) as showerror, patch(
                "rb_converter_gui.converter_manifest.load_manifest",
                return_value=converter_manifest.empty_manifest(),
            ), patch(
                "rb_converter_gui.rb.prepare", return_value=(plan, [])
            ), patch(
                "rb_converter_gui.rb.share_output_root"
            ), patch(
                "rb_converter_gui.rb.collect_batch_unique",
                return_value=plan.unique,
            ), patch(
                "rb_converter_gui.rb.share_cover_caches"
            ):
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
                    raise rb.CancelledError("conversion cancelled during preview")

                def run_inline(target=None, **_kwargs):
                    class _T:
                        def start(self_inner):
                            target()

                    return _T()

                with patch(
                    "rb_converter_gui.rb.build_conversion_preview",
                    side_effect=preview_then_cancel,
                ), patch(
                    "rb_converter_gui.threading.Thread", side_effect=run_inline
                ):
                    root = tk.Tk()
                    root.withdraw()
                    app = ConverterApp(root, documents_accessible=False)
                    app.xml_var.set("/tmp/test.xml")
                    _seed_track_selection(app)
                    _mark_output_folder_valid(app)
                    app._start_convert()
                    root.update_idletasks()
                    for _ in range(20):
                        root.update()

                    convert_unique.assert_not_called()
                    apply_xml.assert_not_called()
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
        """Given convert_unique + apply_xml + write_import_xml succeed: When
        cancel_event is set before finish scheduling: Then the GUI takes the
        _finish_cancelled path (status Cancelled.), not _finish_ok / Done."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = _mock_convert_plan(n_unique=1)

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
                "rb_converter_gui.converter_manifest.save_manifest"
            ), patch(
                "rb_converter_gui.rb.build_conversion_preview",
                return_value=_empty_conversion_preview(selected=1),
            ), patch.object(
                ConverterApp, "_show_conversion_preview"
            ), patch(
                "rb_converter_gui.rb.convert_unique",
                return_value=rb.ConvertStats(converted=1),
            ), patch(
                "rb_converter_gui.rb.apply_xml", return_value=1
            ) as apply_xml, patch(
                "rb_converter_gui.rb.write_import_xml"
            ) as write_xml, patch(
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

                    def write_then_cancel(*_a, **_k):
                        app._cancel_event.set()

                    write_xml.side_effect = write_then_cancel
                    _mark_output_folder_valid(app)
                    _start_convert_and_confirm(app, root)

                    apply_xml.assert_called()
                    write_xml.assert_called()
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


class PrepareWriteBoundaryTests(unittest.TestCase):
    def test_confirm_writes_in_order_without_reprepare(
        self,
    ) -> None:
        """Given a prepared payload: When the user confirms: Then save_manifest,
        convert_unique(items=prepared), apply_xml on successes, and
        write_import_xml run in order without a second prepare/ffprobe; action
        is rechecked inside convert_unique."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from contextlib import ExitStack
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = _mock_convert_plan(n_unique=1)
        preview = rb.ConversionPreview(
            selected=1,
            resolved=1,
            unique_outputs=1,
            duplicates=0,
            missing=0,
            items=[],
        )
        call_order: list[str] = []
        planned_action_calls: list[object] = []

        def convert_side_effect(plan_arg, force, **kwargs):
            call_order.append("convert_unique")
            items = kwargs.get("items") or plan_arg.unique
            for item in items:
                # Execution-time recheck (files may change while preview is open).
                planned_action_calls.append(
                    rb.planned_action(plan_arg, item, force)
                )
            return rb.ConvertStats(converted=1, succeeded={("sk", "wav")})

        root = None
        try:

            def run_inline(target=None, **_kwargs):
                class _T:
                    def start(self_inner):
                        target()

                return _T()

            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "rb_converter_gui.check_for_update",
                        return_value=UpdateCheckResult(kind="up_to_date"),
                    )
                )
                stack.enter_context(
                    patch("rb_converter_gui.load_preferences", return_value={})
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.resolve_startup_paths",
                        return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
                    )
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.discover_xml_candidates",
                        return_value=[],
                    )
                )
                stack.enter_context(patch("rb_converter_gui.save_preferences"))
                stack.enter_context(
                    patch.object(
                        ConverterApp,
                        "_selected_playlists",
                        return_value=[("ROOT", "Test")],
                    )
                )
                prepare = stack.enter_context(
                    patch("rb_converter_gui.rb.prepare", return_value=(plan, []))
                )
                stack.enter_context(patch("rb_converter_gui.rb.share_output_root"))
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.collect_batch_unique",
                        return_value=plan.unique,
                    )
                )
                stack.enter_context(patch("rb_converter_gui.rb.share_cover_caches"))
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.build_conversion_preview",
                        return_value=preview,
                    )
                )
                save_manifest = stack.enter_context(
                    patch(
                        "rb_converter_gui.converter_manifest.save_manifest",
                        side_effect=lambda *a, **k: call_order.append(
                            "save_manifest"
                        ),
                    )
                )
                convert_unique = stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.convert_unique",
                        side_effect=convert_side_effect,
                    )
                )
                apply_xml = stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.apply_xml",
                        side_effect=lambda *a, **k: (
                            call_order.append("apply_xml"),
                            1,
                        )[1],
                    )
                )
                write_xml = stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.write_import_xml",
                        side_effect=lambda *a, **k: call_order.append(
                            "write_import_xml"
                        ),
                    )
                )
                stack.enter_context(
                    patch.object(
                        ConverterApp, "_show_conversion_preview", create=True
                    )
                )
                stack.enter_context(
                    patch.object(ConverterApp, "_show_done_dialog")
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.threading.Thread",
                        side_effect=run_inline,
                    )
                )

                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                _seed_track_selection(app)
                _mark_output_folder_valid(app)
                app._start_convert()
                root.update_idletasks()
                for _ in range(20):
                    root.update()

                prepared = getattr(app, "_prepared_conversion", None)
                self.assertIsNotNone(prepared)
                prepare_count = prepare.call_count
                self.assertEqual(prepare_count, 1)
                save_manifest.assert_not_called()
                convert_unique.assert_not_called()

                prepared_items = list(prepared.items)
                _confirm_conversion_preview(app)
                root.update_idletasks()
                for _ in range(20):
                    root.update()

                self.assertEqual(
                    call_order,
                    [
                        "save_manifest",
                        "convert_unique",
                        "apply_xml",
                        "write_import_xml",
                    ],
                )
                self.assertEqual(prepare.call_count, prepare_count)
                kwargs = convert_unique.call_args.kwargs
                self.assertEqual(list(kwargs.get("items")), prepared_items)
                self.assertEqual(planned_action_calls, ["transcode"])
                apply_xml.assert_called()
                write_xml.assert_called()
                self.assertIsNone(app._prepared_conversion)
                self.assertFalse(app._busy)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


class ConversionPreviewDialogTests(unittest.TestCase):
    def test_preview_modal_table_and_disabled_controls(self) -> None:
        """Given prepare finishes: When the preview opens: Then the modal shows
        a unique-output table (file / action / quality / size), does not take
        a Tk grab, disables editing controls, hides Convert behind the dialog,
        and Back (same path as Escape/close) discards the prepared payload."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from contextlib import ExitStack
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = _mock_convert_plan(n_unique=1)
        preview = rb.ConversionPreview(
            selected=3,
            resolved=3,
            unique_outputs=3,
            duplicates=0,
            missing=0,
            items=[
                rb.ConversionPreviewItem(
                    relative_dest="WAV/A - One.wav",
                    action="reuse",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=1000,
                    size_display="0.0 MB",
                    source_display="one.flac",
                ),
                rb.ConversionPreviewItem(
                    relative_dest="WAV/B - Two.wav",
                    action="copy",
                    bit_depth=24,
                    sample_rate=48000,
                    size_bytes=288000,
                    size_display="≈ 0.3 MB",
                    source_display="two.wav",
                ),
                rb.ConversionPreviewItem(
                    relative_dest="WAV/C - Three.wav",
                    action="transcode",
                    bit_depth=24,
                    sample_rate=48000,
                    size_bytes=None,
                    size_display="—",
                    source_display="three.flac",
                ),
            ],
        )

        def run_inline(target=None, **_kwargs):
            class _T:
                def start(self_inner):
                    target()

            return _T()

        def find_toplevel(parent, title: str):
            for child in parent.winfo_children():
                if isinstance(child, tk.Toplevel):
                    try:
                        if child.title() == title:
                            return child
                    except tk.TclError:
                        continue
            return None

        def find_treeview(widget):
            if isinstance(widget, ttk.Treeview):
                return widget
            for child in widget.winfo_children():
                found = find_treeview(child)
                if found is not None:
                    return found
            return None

        root = None
        try:
            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "rb_converter_gui.check_for_update",
                        return_value=UpdateCheckResult(kind="up_to_date"),
                    )
                )
                stack.enter_context(
                    patch("rb_converter_gui.load_preferences", return_value={})
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.resolve_startup_paths",
                        return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
                    )
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.discover_xml_candidates",
                        return_value=[],
                    )
                )
                stack.enter_context(patch("rb_converter_gui.save_preferences"))
                stack.enter_context(
                    patch.object(
                        ConverterApp,
                        "_selected_playlists",
                        return_value=[("ROOT", "Test")],
                    )
                )
                stack.enter_context(
                    patch("rb_converter_gui.rb.prepare", return_value=(plan, []))
                )
                stack.enter_context(patch("rb_converter_gui.rb.share_output_root"))
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.collect_batch_unique",
                        return_value=plan.unique,
                    )
                )
                stack.enter_context(patch("rb_converter_gui.rb.share_cover_caches"))
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.build_conversion_preview",
                        return_value=preview,
                    )
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.threading.Thread",
                        side_effect=run_inline,
                    )
                )

                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                _seed_track_selection(app)
                _mark_output_folder_valid(app)
                app._start_convert()
                _pump_ui(root)

                dlg = find_toplevel(root, "Conversion preview")
                self.assertIsNotNone(dlg)
                self.assertIn(dlg.grab_current(), (None, ""))

                table = find_treeview(dlg)
                self.assertIsNotNone(table)
                self.assertEqual(table.heading("#0", "text"), "Input file")
                rows = [
                    (
                        table.item(iid, "text") or table.item(iid, "values")[0],
                        table.item(iid, "values"),
                    )
                    for iid in table.get_children("")
                ]
                # Normalize to (file, action, quality, size) regardless of
                # whether file is tree text (#0) or a values column.
                normalized = []
                for text, values in rows:
                    vals = list(values)
                    if text and (not vals or vals[0] != text):
                        normalized.append((text, *vals[:3]))
                    else:
                        normalized.append(tuple(vals[:4]))
                self.assertEqual(
                    normalized,
                    [
                        (
                            "one.flac",
                            "Reuse existing",
                            "16-bit / 44.1 kHz",
                            "0.0 MB",
                        ),
                        (
                            "two.wav",
                            "Copy",
                            "24-bit / 48 kHz",
                            "≈ 0.3 MB",
                        ),
                        (
                            "three.flac",
                            "Transcode",
                            "24-bit / 48 kHz",
                            "—",
                        ),
                    ],
                )

                self.assertEqual(str(app.xml_entry.cget("state")), "disabled")
                self.assertEqual(str(app.wav_dir_entry.cget("state")), "disabled")
                self.assertEqual(str(app.xml_browse_btn.cget("state")), "disabled")
                self.assertEqual(
                    str(app.wav_dir_browse_btn.cget("state")), "disabled"
                )
                self.assertEqual(str(app.search_entry.cget("state")), "disabled")
                self.assertEqual(
                    str(app.track_search_entry.cget("state")), "disabled"
                )
                self.assertEqual(
                    str(app.format_wav_radio.cget("state")), "disabled"
                )
                self.assertEqual(
                    str(app.format_aiff_radio.cget("state")), "disabled"
                )
                self.assertEqual(
                    str(app.bit_depth_combo.cget("state")), "disabled"
                )
                self.assertEqual(
                    str(app.sample_rate_combo.cget("state")), "disabled"
                )
                self.assertEqual(str(app.refresh_btn.cget("state")), "disabled")
                self.assertIn("disabled", app.playlist_tree.state())
                self.assertIn("disabled", app.tracklist_tree.state())
                self.assertEqual(app.convert_btn.winfo_manager(), "")
                self.assertEqual(str(app.cancel_btn.cget("state")), "normal")
                self.assertTrue(dlg.bind("<Escape>"))

                def find_convert_btn(widget):
                    try:
                        if (
                            isinstance(widget, ttk.Button)
                            and str(widget.cget("text")) == "Convert"
                        ):
                            return widget
                    except tk.TclError:
                        pass
                    for child in widget.winfo_children():
                        found = find_convert_btn(child)
                        if found is not None:
                            return found
                    return None

                preview_convert = find_convert_btn(dlg)
                self.assertIsNotNone(preview_convert)
                self.assertEqual(str(preview_convert.cget("state")), "normal")

                def click_back(widget) -> bool:
                    try:
                        if (
                            isinstance(widget, ttk.Button)
                            and str(widget.cget("text")) == "Back"
                        ):
                            widget.invoke()
                            return True
                    except tk.TclError:
                        pass
                    for child in widget.winfo_children():
                        if click_back(child):
                            return True
                    return False

                self.assertTrue(click_back(dlg))
                _pump_ui(root, times=10)
                self.assertIsNone(app._prepared_conversion)
                self.assertFalse(app._busy)
                self.assertIsNone(find_toplevel(root, "Conversion preview"))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_preview_disables_convert_when_output_space_insufficient(self) -> None:
        """Given write size above free space: When the preview opens: Then an
        issue message is shown and Convert is disabled."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from contextlib import ExitStack
        from types import SimpleNamespace
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        plan = _mock_convert_plan(n_unique=1)
        preview = rb.ConversionPreview(
            selected=1,
            resolved=1,
            unique_outputs=1,
            duplicates=0,
            missing=0,
            items=[
                rb.ConversionPreviewItem(
                    relative_dest="WAV/A - One.wav",
                    action="transcode",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=50_000_000,
                    size_display="≈ 47.7 MB",
                    source_display="one.flac",
                ),
            ],
        )

        def run_inline(target=None, **_kwargs):
            class _T:
                def start(self_inner):
                    target()

            return _T()

        def find_toplevel(parent, title: str):
            for child in parent.winfo_children():
                if isinstance(child, tk.Toplevel):
                    try:
                        if child.title() == title:
                            return child
                    except tk.TclError:
                        continue
            return None

        def find_label_with_text(widget, needle: str):
            try:
                if isinstance(widget, ttk.Label) and needle in str(widget.cget("text")):
                    return widget
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                found = find_label_with_text(child, needle)
                if found is not None:
                    return found
            return None

        def find_convert_btn(widget):
            try:
                if (
                    isinstance(widget, ttk.Button)
                    and str(widget.cget("text")) == "Convert"
                ):
                    return widget
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                found = find_convert_btn(child)
                if found is not None:
                    return found
            return None

        root = None
        try:
            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "rb_converter_gui.check_for_update",
                        return_value=UpdateCheckResult(kind="up_to_date"),
                    )
                )
                stack.enter_context(
                    patch("rb_converter_gui.load_preferences", return_value={})
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.resolve_startup_paths",
                        return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
                    )
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.discover_xml_candidates",
                        return_value=[],
                    )
                )
                stack.enter_context(patch("rb_converter_gui.save_preferences"))
                stack.enter_context(
                    patch.object(
                        ConverterApp,
                        "_selected_playlists",
                        return_value=[("ROOT", "Test")],
                    )
                )
                stack.enter_context(
                    patch("rb_converter_gui.rb.prepare", return_value=(plan, []))
                )
                stack.enter_context(patch("rb_converter_gui.rb.share_output_root"))
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.collect_batch_unique",
                        return_value=plan.unique,
                    )
                )
                stack.enter_context(patch("rb_converter_gui.rb.share_cover_caches"))
                stack.enter_context(
                    patch(
                        "rb_converter_gui.rb.build_conversion_preview",
                        return_value=preview,
                    )
                )
                stack.enter_context(
                    patch(
                        "convert_plan.shutil.disk_usage",
                        return_value=SimpleNamespace(free=1_000_000),
                    )
                )
                stack.enter_context(
                    patch(
                        "rb_converter_gui.threading.Thread",
                        side_effect=run_inline,
                    )
                )

                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                _seed_track_selection(app)
                _mark_output_folder_valid(app)
                app._start_convert()
                _pump_ui(root)

                dlg = find_toplevel(root, "Conversion preview")
                self.assertIsNotNone(dlg)
                issue = find_label_with_text(
                    dlg, "Not enough free space in the output folder"
                )
                self.assertIsNotNone(issue)
                convert_btn = find_convert_btn(dlg)
                self.assertIsNotNone(convert_btn)
                self.assertEqual(str(convert_btn.cget("state")), "disabled")
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
