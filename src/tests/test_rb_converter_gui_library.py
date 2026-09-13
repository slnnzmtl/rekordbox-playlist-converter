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
                legacy.mkdir()
                (legacy / "old.wav").write_bytes(b"RIFF")
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

    def test_done_dialog_reveal_actions_target_library_and_xml(self) -> None:
        """Given a successful convert dialog: When shown: Then Reveal audio folder
        opens the WAV or AIFF media_dir, and Reveal import XML opens the
        generated XML."""
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
                wav_folder = lib / "WAV"
                aiff_folder = lib / "AIFF"
                xml = lib / "rekordbox-import.xml"

                app._show_done_dialog("Done body", wav_folder, xml)
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                self.assertTrue(click_button(dlg, "Reveal audio folder"))
                reveal.assert_called_with(wav_folder)

                app._show_done_dialog("Done body", aiff_folder, xml)
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                self.assertTrue(click_button(dlg, "Reveal audio folder"))
                reveal.assert_called_with(aiff_folder)

                app._show_done_dialog("Done body", wav_folder, xml)
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                self.assertTrue(click_button(dlg, "Reveal import XML"))
                reveal.assert_called_with(xml)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
