#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))


from convert.format_policy import planned_action
from convert.models import ConversionPreview, ConversionPreviewItem, ConvertStats
from gui_tk import (
    app_patches,
    confirm_conversion_preview,
    mark_output_folder_valid,
    merge_patches,
    mock_convert_plan,
    mock_prepared_conversion,
    pump_ui,
    run_inline_thread,
    seed_track_selection,
    startup_patches,
    tk_available,
)

class PrepareWriteBoundaryTests(unittest.TestCase):
    def test_confirm_writes_in_order_without_reprepare(
        self,
    ) -> None:
        """Given a prepared payload: When the user confirms: Then
        execute_prepared runs once with that payload (no second prepare);
        inner convert_unique / apply_xml / write_import_xml are not called
        from the GUI write worker."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        plan = mock_convert_plan(n_unique=1)
        prepared = mock_prepared_conversion(n_unique=1)
        preview = prepared.preview
        call_order: list[str] = []
        planned_action_calls: list[object] = []

        def execute_side_effect(prepared, **kwargs):
            call_order.append("execute_prepared")
            items = list(prepared.items)
            for item in items:
                planned_action_calls.append(
                    planned_action(prepared.plans[0], item, kwargs.get("force", False))
                )
            return ConvertStats(converted=1, succeeded={("sk", "wav")})

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"return_value": (prepared, [])},
                        "execute_prepared": {
                            "create": True,
                            "side_effect": execute_side_effect,
                        },
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ) as mocks, patch.object(
                ConverterApp,
                "_selected_playlists",
                return_value=[("ROOT", "Test")],
            ), patch.object(
                ConverterApp, "_show_conversion_preview", create=True
            ), patch.object(ConverterApp, "_show_done_dialog"):
                prepare = mocks["prepare_batch"]
                execute = mocks["execute_prepared"]

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

                prepared = getattr(app, "_prepared_conversion", None)
                self.assertIsNotNone(prepared)
                prepare_count = prepare.call_count
                self.assertEqual(prepare_count, 1)
                execute.assert_not_called()

                prepared_items = list(prepared.items)
                confirm_conversion_preview(app)
                root.update_idletasks()
                for _ in range(20):
                    root.update()

                self.assertEqual(call_order, ["execute_prepared"])
                self.assertEqual(prepare.call_count, prepare_count)
                execute.assert_called_once()
                self.assertEqual(list(execute.call_args.args[0].items), prepared_items)
                self.assertEqual(planned_action_calls, ["recreate_missing"])
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from rb_converter_gui import ConverterApp

        preview = ConversionPreview(
            selected=3,
            resolved=3,
            unique_outputs=3,
            duplicates=0,
            missing=0,
            items=[
                ConversionPreviewItem(
                    relative_dest="WAV/A - One.wav",
                    action="reuse",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=1000,
                    size_display="0.0 MB",
                    source_display="one.flac",
                    reason="Output is already current",
                    write_kind="none",
                    output_format="wav",
                ),
                ConversionPreviewItem(
                    relative_dest="WAV/B - Two.wav",
                    action="recreate_missing",
                    bit_depth=24,
                    sample_rate=48000,
                    size_bytes=288000,
                    size_display="≈ 0.3 MB",
                    source_display="two.wav",
                    reason="Destination file is missing",
                    write_kind="audio",
                    reason_code="dest_missing",
                    output_format="wav",
                ),
                ConversionPreviewItem(
                    relative_dest="WAV/C - Three.wav",
                    action="transcode",
                    bit_depth=24,
                    sample_rate=48000,
                    size_bytes=None,
                    size_display="—",
                    source_display="three.flac",
                    reason="Source file changed",
                    write_kind="audio",
                    output_format="wav",
                ),
            ],
        )
        prepared = replace(mock_prepared_conversion(n_unique=3), preview=preview)

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
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"return_value": (prepared, [])},
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ), patch.object(
                ConverterApp,
                "_selected_playlists",
                return_value=[("ROOT", "Test")],
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                app._start_convert()
                pump_ui(root)

                dlg = find_toplevel(root, "Conversion preview")
                self.assertIsNotNone(dlg)
                self.assertIn(dlg.grab_current(), (None, ""))

                table = find_treeview(dlg)
                self.assertIsNotNone(table)
                self.assertEqual(table.heading("#0", "text"), "Input file")
                self.assertEqual(table.heading("format", "text"), "Format")
                self.assertEqual(table.heading("action", "text"), "Action")
                self.assertEqual(table.heading("reason", "text"), "Reason")
                self.assertEqual(table.heading("quality", "text"), "Quality")
                self.assertEqual(table.heading("size", "text"), "Size")
                rows = [
                    (
                        table.item(iid, "text"),
                        list(table.item(iid, "values")),
                    )
                    for iid in table.get_children("")
                ]
                self.assertEqual(
                    rows,
                    [
                        (
                            "one.flac",
                            [
                                "WAV",
                                "Reuse existing",
                                "Output is already current",
                                "16-bit / 44.1 kHz",
                                "0.0 MB",
                            ],
                        ),
                        (
                            "two.wav",
                            [
                                "WAV",
                                "Recreate missing",
                                "Destination file is missing",
                                "24-bit / 48 kHz",
                                "≈ 0.3 MB",
                            ],
                        ),
                        (
                            "three.flac",
                            [
                                "WAV",
                                "Transcode",
                                "Source file changed",
                                "24-bit / 48 kHz",
                                "—",
                            ],
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
                pump_ui(root, times=10)
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from types import SimpleNamespace
        from rb_converter_gui import ConverterApp

        preview = ConversionPreview(
            selected=1,
            resolved=1,
            unique_outputs=1,
            duplicates=0,
            missing=0,
            items=[
                ConversionPreviewItem(
                    relative_dest="WAV/A - One.wav",
                    action="transcode",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=50_000_000,
                    size_display="≈ 47.7 MB",
                    source_display="one.flac",
                    write_kind="audio",
                    output_format="wav",
                ),
            ],
        )
        prepared = replace(mock_prepared_conversion(n_unique=1), preview=preview)

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
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"return_value": (prepared, [])},
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ), patch.object(
                ConverterApp,
                "_selected_playlists",
                return_value=[("ROOT", "Test")],
            ), patch(
                "convert.preview.shutil.disk_usage",
                return_value=SimpleNamespace(free=1_000_000),
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                app._start_convert()
                pump_ui(root)

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

    def test_preview_disables_convert_when_conflicts_remain(self) -> None:
        """Given an unresolved destination conflict: When the preview opens:
        Then preview_block_message is shown and Convert is disabled."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from rb_converter_gui import ConverterApp

        preview = ConversionPreview(
            selected=1,
            resolved=1,
            unique_outputs=1,
            duplicates=0,
            missing=0,
            items=[
                ConversionPreviewItem(
                    relative_dest="WAV/A - One.wav",
                    action="external_modification_conflict",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=1000,
                    size_display="0.0 MB",
                    source_display="one.flac",
                    reason="Destination was changed outside this app",
                    write_kind="none",
                    output_format="wav",
                ),
            ],
        )
        prepared = replace(mock_prepared_conversion(n_unique=1), preview=preview)

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
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"return_value": (prepared, [])},
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ), patch.object(
                ConverterApp,
                "_selected_playlists",
                return_value=[("ROOT", "Test")],
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                app._start_convert()
                pump_ui(root)

                dlg = find_toplevel(root, "Conversion preview")
                self.assertIsNotNone(dlg)
                issue = find_label_with_text(dlg, "unresolved conflict")
                self.assertIsNotNone(issue)
                convert_btn = find_convert_btn(dlg)
                self.assertIsNotNone(convert_btn)
                self.assertEqual(str(convert_btn.cget("state")), "disabled")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_preview_shows_size_info_when_space_ok(self) -> None:
        """Given enough free space and audio to write: When preview opens:
        Then a neutral size info line is shown and Convert stays enabled."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from types import SimpleNamespace
        from rb_converter_gui import ConverterApp

        preview = ConversionPreview(
            selected=1,
            resolved=1,
            unique_outputs=1,
            duplicates=0,
            missing=0,
            items=[
                ConversionPreviewItem(
                    relative_dest="WAV/A - One.wav",
                    action="transcode",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=5_000_000,
                    size_display="≈ 4.8 MB",
                    source_display="one.flac",
                    write_kind="audio",
                    output_format="wav",
                ),
            ],
        )
        prepared = replace(mock_prepared_conversion(n_unique=1), preview=preview)

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
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"return_value": (prepared, [])},
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ), patch.object(
                ConverterApp,
                "_selected_playlists",
                return_value=[("ROOT", "Test")],
            ), patch(
                "convert.preview.shutil.disk_usage",
                return_value=SimpleNamespace(free=10_000_000),
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                app._start_convert()
                pump_ui(root)

                dlg = find_toplevel(root, "Conversion preview")
                self.assertIsNotNone(dlg)
                info = find_label_with_text(dlg, "needed after conversion")
                self.assertIsNotNone(info)
                convert_btn = find_convert_btn(dlg)
                self.assertIsNotNone(convert_btn)
                self.assertEqual(str(convert_btn.cget("state")), "normal")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_preview_disables_convert_when_nothing_to_convert(self) -> None:
        """Given only missing sources: When preview opens: Then block message
        is shown, missing rows appear, and Convert is disabled."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from rb_converter_gui import ConverterApp

        preview = ConversionPreview(
            selected=2,
            resolved=0,
            unique_outputs=0,
            duplicates=0,
            missing=2,
            items=[
                ConversionPreviewItem(
                    relative_dest="—",
                    action="missing_source",
                    bit_depth=0,
                    sample_rate=0,
                    size_display="—",
                    source_display="gone.flac",
                    reason="Source file is missing",
                    write_kind="none",
                    reason_code="source_missing",
                    output_format="",
                ),
            ],
        )
        prepared = replace(mock_prepared_conversion(n_unique=0), preview=preview)

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
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "prepare_batch": {"return_value": (prepared, [])},
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ), patch.object(
                ConverterApp,
                "_selected_playlists",
                return_value=[("ROOT", "Test")],
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                app._start_convert()
                pump_ui(root)

                dlg = find_toplevel(root, "Conversion preview")
                self.assertIsNotNone(dlg)
                block = find_label_with_text(dlg, "Nothing to convert")
                self.assertIsNotNone(block)
                table = find_treeview(dlg)
                self.assertIsNotNone(table)
                rows = [table.item(iid, "text") for iid in table.get_children("")]
                self.assertIn("gone.flac", rows)
                convert_btn = find_convert_btn(dlg)
                self.assertIsNotNone(convert_btn)
                self.assertEqual(str(convert_btn.cget("state")), "disabled")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
