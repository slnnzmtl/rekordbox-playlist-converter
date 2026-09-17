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

from gui_tk import (
    app_patches,
    click_button,
    startup_patches,
    tk_available,
)


class GuiLibraryValidationTests(unittest.TestCase):
    def test_validation_error_disables_convert(self) -> None:
        """Given a legacy folder without a manifest: When validation finishes:
        Then Convert is disabled and an inline error is shown."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tempfile
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                legacy = Path(tmp) / "legacy"
                (legacy / "WAV").mkdir(parents=True)
                (legacy / "WAV" / "old.wav").write_bytes(b"RIFF")
                with app_patches(**startup_patches(), save_preferences=None):
                    root = tk.Tk()
                    root.withdraw()
                    app = ConverterApp(root, documents_accessible=False)
                    app.library_dir_var.set(str(legacy))
                    after_id = app._wav_dir_validate_after_id
                    if after_id is not None:
                        app.root.after_cancel(after_id)
                        app._wav_dir_validate_after_id = None
                    err = __import__(
                        "converter_manifest", fromlist=["validate_library_folder"]
                    ).validate_library_folder(legacy)
                    self.assertIsNotNone(err)
                    app._wav_dir_checking = False
                    app._wav_dir_valid = False
                    app._set_wav_dir_error(err)
                    app._update_convert_enabled()
                    self.assertEqual(str(app.convert_btn.cget("state")), "disabled")
                    self.assertIn(
                        "new empty output folder",
                        app.wav_dir_error_var.get().lower(),
                    )
                    self.assertTrue(app.wav_dir_error_label.winfo_manager())

                    empty = Path(tmp) / "empty-lib"
                    empty.mkdir()
                    app.library_dir_var.set(str(empty))
                    after_id = app._wav_dir_validate_after_id
                    if after_id is not None:
                        app.root.after_cancel(after_id)
                        app._wav_dir_validate_after_id = None
                    self.assertIsNone(
                        __import__(
                            "converter_manifest", fromlist=["validate_library_folder"]
                        ).validate_library_folder(empty)
                    )
                    app._wav_dir_checking = False
                    app._wav_dir_valid = True
                    app._set_wav_dir_error("")
                    app._update_convert_enabled()
                    self.assertEqual(str(app.convert_btn.cget("state")), "normal")
                    self.assertEqual(app.wav_dir_error_label.winfo_manager(), "")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_done_dialog_reveal_opens_output_folder(self) -> None:
        """Given a successful convert dialog: When Reveal output folder: Then
        Finder opens the library root (parent of WAV/AIFF and import XML)."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(),
                open_in_finder=None,
            ) as mocks, patch.object(tk.Toplevel, "wait_window"):
                reveal = mocks["open_in_finder"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                lib = Path("/tmp/library-root")

                app._show_done_dialog("Done body", lib)
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                self.assertTrue(click_button(dlg, "Reveal output folder"))
                reveal.assert_called_with(lib)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_done_dialog_uses_scrollable_list_for_long_report(self) -> None:
        """Given a Partial report with many missing paths: When the dialog
        opens: Then lines appear in a Listbox and Reveal stays available."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from rb_converter_gui import ConverterApp

        lines = [
            "Tekno : Acid core [AIFF]: 1 reused, "
            "generated playlist was not created or refreshed",
            "Missing skipped:",
        ]
        for i in range(20):
            lines.append(f"missing source file: /Volumes/SSD/track{i}.flac")
        lines.extend(
            [
                "",
                "Import into Rekordbox:",
                "1. Preferences → View → Layout → enable rekordbox xml",
            ]
        )
        message = "\n".join(lines)

        def find_listbox(widget):
            if isinstance(widget, tk.Listbox):
                return widget
            for child in widget.winfo_children():
                found = find_listbox(child)
                if found is not None:
                    return found
            return None

        root = None
        try:
            with app_patches(
                **startup_patches(),
                open_in_finder=None,
            ), patch.object(tk.Toplevel, "wait_window"):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                lib = Path("/tmp/library-root")

                app._show_done_dialog(message, lib, title="Partial")
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                self.assertEqual(dlg.title(), "Partial")
                listbox = find_listbox(dlg)
                self.assertIsNotNone(listbox)
                listed = list(listbox.get(0, tk.END))
                self.assertIn(lines[0], listed)
                self.assertIn(lines[2], listed)
                self.assertIn("missing source file: /Volumes/SSD/track19.flac", listed)
                has_reveal = False
                has_ok = False
                has_guide = False

                def scan_buttons(widget) -> None:
                    nonlocal has_reveal, has_ok, has_guide
                    try:
                        if isinstance(widget, ttk.Button):
                            text = str(widget.cget("text"))
                            if text == "Reveal output folder":
                                has_reveal = True
                            elif text == "OK":
                                has_ok = True
                            elif text == "Open usage guide":
                                has_guide = True
                    except tk.TclError:
                        pass
                    for child in widget.winfo_children():
                        scan_buttons(child)

                scan_buttons(dlg)
                self.assertTrue(has_reveal)
                self.assertTrue(has_ok)
                self.assertTrue(has_guide)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_done_dialog_shows_result_table_with_statuses(self) -> None:
        """Given finish result groups: When the Done dialog opens: Then
        playlist status lines are tree headers with tracks nested under them."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import tkinter.ttk as ttk
        from convert.models import FinishResultGroup, FinishResultRow
        from rb_converter_gui import ConverterApp

        groups = [
            FinishResultGroup(
                header="psy:ukraine [WAV]: 1 converted, 1 reused",
                rows=[
                    FinishResultRow(
                        track="a.flac", status="Converted", detail="WAV/a.wav"
                    ),
                    FinishResultRow(
                        track="b.flac", status="Reused", detail="WAV/b.wav"
                    ),
                ],
            ),
            FinishResultGroup(
                header="Techno [WAV]: 1 converted",
                rows=[
                    FinishResultRow(
                        track="gone.flac",
                        status="Missing",
                        detail="/music/gone.flac",
                    ),
                ],
            ),
        ]

        def find_tree(widget):
            if isinstance(widget, ttk.Treeview):
                return widget
            for child in widget.winfo_children():
                found = find_tree(child)
                if found is not None:
                    return found
            return None

        def find_label(widget, needle: str):
            try:
                if isinstance(widget, ttk.Label) and needle in str(widget.cget("text")):
                    return widget
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                found = find_label(child, needle)
                if found is not None:
                    return found
            return None

        root = None
        try:
            with app_patches(
                **startup_patches(),
                open_in_finder=None,
            ), patch.object(tk.Toplevel, "wait_window"):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                lib = Path("/tmp/library-root")
                app._show_done_dialog(
                    "",
                    lib,
                    title="Done",
                    result_groups=groups,
                    guidance="Import into Rekordbox:\n1. Preferences → View",
                )
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                table = find_tree(dlg)
                self.assertIsNotNone(table)
                self.assertEqual(table.heading("#0", "text"), "Track")
                self.assertEqual(table.heading("status", "text"), "Status")
                self.assertEqual(table.heading("detail", "text"), "Detail")
                roots = list(table.get_children(""))
                self.assertEqual(len(roots), 2)
                self.assertEqual(
                    table.item(roots[0], "text"),
                    "psy:ukraine [WAV]: 1 converted, 1 reused",
                )
                self.assertFalse(table.item(roots[0], "open"))
                self.assertFalse(table.item(roots[1], "open"))
                self.assertGreaterEqual(int(table.column("#0", "width")), 360)
                # Leaves exist under collapsed headers; expand to read them.
                table.item(roots[0], open=True)
                leaves = [
                    (table.item(iid, "text"), list(table.item(iid, "values")))
                    for iid in table.get_children(roots[0])
                ]
                self.assertEqual(
                    leaves,
                    [
                        ("a.flac", ["Converted", "WAV/a.wav"]),
                        ("b.flac", ["Reused", "WAV/b.wav"]),
                    ],
                )
                self.assertEqual(
                    table.item(roots[1], "text"), "Techno [WAV]: 1 converted"
                )
                table.item(roots[1], open=True)
                self.assertEqual(
                    [
                        (table.item(iid, "text"), list(table.item(iid, "values")))
                        for iid in table.get_children(roots[1])
                    ],
                    [("gone.flac", ["Missing", "/music/gone.flac"])],
                )
                self.assertIsNotNone(find_label(dlg, "Import into Rekordbox"))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
