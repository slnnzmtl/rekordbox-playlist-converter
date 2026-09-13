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

from gui_tk import (
    app_patches,
    confirm_conversion_preview,
    mark_output_folder_valid,
    merge_patches,
    mock_convert_plan,
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
        """Given a prepared payload: When the user confirms: Then save_manifest,
        convert_unique(items=prepared), apply_xml on successes, and
        write_import_xml run in order without a second prepare/ffprobe; action
        is rechecked inside convert_unique."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        plan = mock_convert_plan(n_unique=1)
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
                planned_action_calls.append(
                    rb.planned_action(plan_arg, item, force)
                )
            return rb.ConvertStats(converted=1, succeeded={("sk", "wav")})

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "rb.prepare": {"return_value": (plan, [])},
                        "rb.share_output_root": None,
                        "rb.collect_batch_unique": plan.unique,
                        "rb.share_cover_caches": None,
                        "rb.build_conversion_preview": preview,
                        "converter_manifest.save_manifest": {
                            "side_effect": lambda *a, **k: call_order.append(
                                "save_manifest"
                            )
                        },
                        "rb.convert_unique": {"side_effect": convert_side_effect},
                        "rb.apply_xml": {
                            "side_effect": lambda *a, **k: (
                                call_order.append("apply_xml"),
                                1,
                            )[1]
                        },
                        "rb.write_import_xml": {
                            "side_effect": lambda *a, **k: call_order.append(
                                "write_import_xml"
                            )
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
                prepare = mocks["rb.prepare"]
                save_manifest = mocks["converter_manifest.save_manifest"]
                convert_unique = mocks["rb.convert_unique"]
                apply_xml = mocks["rb.apply_xml"]
                write_xml = mocks["rb.write_import_xml"]

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
                save_manifest.assert_not_called()
                convert_unique.assert_not_called()

                prepared_items = list(prepared.items)
                confirm_conversion_preview(app)
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from rb_converter_gui import ConverterApp

        plan = mock_convert_plan(n_unique=1)
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
                        "rb.prepare": {"return_value": (plan, [])},
                        "rb.share_output_root": None,
                        "rb.collect_batch_unique": plan.unique,
                        "rb.share_cover_caches": None,
                        "rb.build_conversion_preview": preview,
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
                rows = [
                    (
                        table.item(iid, "text") or table.item(iid, "values")[0],
                        table.item(iid, "values"),
                    )
                    for iid in table.get_children("")
                ]
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

        plan = mock_convert_plan(n_unique=1)
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
                        "rb.prepare": {"return_value": (plan, [])},
                        "rb.share_output_root": None,
                        "rb.collect_batch_unique": plan.unique,
                        "rb.share_cover_caches": None,
                        "rb.build_conversion_preview": preview,
                        "threading.Thread": {"side_effect": run_inline_thread},
                    },
                )
            ), patch.object(
                ConverterApp,
                "_selected_playlists",
                return_value=[("ROOT", "Test")],
            ), patch(
                "convert_plan.shutil.disk_usage",
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

if __name__ == "__main__":
    unittest.main()
