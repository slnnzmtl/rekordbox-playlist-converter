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
    mark_output_folder_valid,
    merge_patches,
    seed_track_selection,
    startup_patches,
    tk_available,
)


class GuiPreferencesPersistTests(unittest.TestCase):
    def test_sampling_combobox_change_persists_preferences(self) -> None:
        """Given ConverterApp, when bit depth / sample rate combobox handlers run,
        then save_preferences is called with the newly selected values."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(),
                save_preferences=None,
            ) as mocks:
                save_prefs = mocks["save_preferences"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                save_prefs.reset_mock()

                app.bit_depth_combo.set("16-bit")
                app._on_bit_depth_selected()
                self.assertEqual(app.bit_depth_var.get(), "16")
                save_prefs.assert_called()
                self.assertEqual(save_prefs.call_args.kwargs.get("bit_depth"), "16")
                save_prefs.reset_mock()

                app.sample_rate_combo.set("44.1 kHz")
                app._on_sample_rate_selected()
                self.assertEqual(app.sample_rate_var.get(), "44100")
                save_prefs.assert_called()
                self.assertEqual(
                    save_prefs.call_args.kwargs.get("sample_rate"), "44100"
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_browse_wav_dir_saves_preferences(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "filedialog.askdirectory": None,
                        "save_preferences": None,
                    },
                )
            ) as mocks:
                ask_dir = mocks["filedialog.askdirectory"]
                save_prefs = mocks["save_preferences"]
                ask_dir.return_value = "/tmp/chosen-wav"
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/source-rekordbox.xml")
                app._browse_wav_dir()
                save_prefs.assert_called_once()
                args = save_prefs.call_args[0]
                kwargs = save_prefs.call_args.kwargs
                self.assertEqual(args[0], Path("/tmp/chosen-wav"))
                self.assertEqual(len(args), 1)
                self.assertIsNone(kwargs.get("source_xml"))
                self.assertEqual(
                    app._resolved_output_paths()[1],
                    Path("/tmp/chosen-wav") / "rekordbox-import.xml",
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_browse_xml_saves_source_xml_preference(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "filedialog.askopenfilename": None,
                        "save_preferences": None,
                    },
                )
            ) as mocks, patch.object(ConverterApp, "_load_playlists"):
                ask_open = mocks["filedialog.askopenfilename"]
                save_prefs = mocks["save_preferences"]
                ask_open.return_value = "/tmp/chosen-rekordbox.xml"
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._browse_xml()
                save_prefs.assert_called_once()
                kwargs = save_prefs.call_args.kwargs
                self.assertEqual(
                    kwargs.get("source_xml"), Path("/tmp/chosen-rekordbox.xml")
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_gui_has_readonly_import_xml_field_that_copies_path(self) -> None:
        """Given ConverterApp: When built: Then Import XML is a disabled entry
        (no Browse), follows wav_dir, and a click copies the full path."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(**startup_patches(), save_preferences=None):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
            self.assertFalse(hasattr(app, "_browse_output"))
            self.assertEqual(str(app.import_xml_entry.cget("state")), "disabled")
            self.assertEqual(str(app.import_xml_entry.cget("cursor")), "hand2")
            app.wav_dir_var.set("/tmp/lib-a")
            expected = str(Path("/tmp/lib-a") / "rekordbox-import.xml")
            self.assertEqual(app.output_var.get(), expected)
            app._copy_import_xml_path()
            self.assertEqual(root.clipboard_get(), expected)
            self.assertEqual(app.status_var.get(), f"Copied path: {expected}")
            self.assertNotIn("\n", app.status_var.get())
            clear_id = app._copy_status_clear_id
            self.assertIsNotNone(clear_id)
            app.root.after_cancel(clear_id)
            app._clear_copy_status()
            self.assertFalse(app.status_var.get().startswith("Copied path:"))
            labels = []

            def walk(w):
                try:
                    text = w.cget("text")
                except tk.TclError:
                    text = ""
                if text:
                    labels.append(text)
                for child in w.winfo_children():
                    walk(child)

            walk(root)
            self.assertIn("Import XML", labels)
            self.assertIn("Max. quality", labels)
            self.assertNotIn("Sampling format", labels)
            self.assertNotIn("Import XML: rekordbox-import.xml", labels)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_changing_wav_dir_changes_derived_xml_path(self) -> None:
        """Given ConverterApp: When wav_dir changes: Then derived import XML
        follows <wav_dir>/rekordbox-import.xml."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(**startup_patches(), save_preferences=None):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.wav_dir_var.set("/tmp/lib-a")
                self.assertEqual(
                    app._resolved_output_paths()[1],
                    Path("/tmp/lib-a") / "rekordbox-import.xml",
                )
                self.assertEqual(
                    app.output_var.get(),
                    str(Path("/tmp/lib-a") / "rekordbox-import.xml"),
                )
                app.wav_dir_var.set("/tmp/lib-b")
                self.assertEqual(
                    app._resolved_output_paths()[1],
                    Path("/tmp/lib-b") / "rekordbox-import.xml",
                )
                self.assertEqual(
                    app.output_var.get(),
                    str(Path("/tmp/lib-b") / "rekordbox-import.xml"),
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_start_convert_saves_preferences_before_worker(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": None,
                        "threading.Thread": None,
                    },
                )
            ) as mocks, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ):
                save_prefs = mocks["save_preferences"]
                thread_cls = mocks["threading.Thread"]
                thread_cls.return_value.start = lambda: None
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                app.wav_dir_var.set("/tmp/typed-wav")
                app.format_var.set("aiff")
                app.bit_depth_var.set("24")
                app.sample_rate_var.set("48000")
                mark_output_folder_valid(app)
                seed_track_selection(app)
                app._start_convert()
                save_prefs.assert_called_once()
                args = save_prefs.call_args[0]
                kwargs = save_prefs.call_args.kwargs
                self.assertEqual(args[0], Path("/tmp/typed-wav"))
                self.assertEqual(len(args), 1)
                self.assertIsNone(kwargs.get("source_xml"))
                self.assertEqual(kwargs.get("output_format"), "aiff")
                self.assertEqual(kwargs.get("bit_depth"), "24")
                self.assertEqual(kwargs.get("sample_rate"), "48000")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_quality_prefs_restored_and_passed_to_prepare(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(
                        preferences={
                            "output_format": "aiff",
                            "bit_depth": "24",
                            "sample_rate": "48000",
                        }
                    ),
                    {
                        "save_preferences": None,
                        "rb.prepare": {"return_value": (None, ["stop"])},
                        "threading.Thread": None,
                    },
                )
            ) as mocks, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ):
                prepare = mocks["rb.prepare"]
                thread_cls = mocks["threading.Thread"]

                def capture_start():
                    target = thread_cls.call_args.kwargs.get("target")
                    if target is None:
                        target = thread_cls.call_args[0][0]
                    target()

                thread_cls.return_value.start = capture_start
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                self.assertEqual(app.format_var.get(), "aiff")
                self.assertEqual(app.bit_depth_var.get(), "24")
                self.assertEqual(app.sample_rate_var.get(), "48000")
                app.xml_var.set("/tmp/test.xml")
                seed_track_selection(app)
                mark_output_folder_valid(app)
                app._start_convert()
                prepare.assert_called()
                kwargs = prepare.call_args.kwargs
                self.assertEqual(kwargs.get("output_format"), "aiff")
                self.assertEqual(kwargs.get("max_bit_depth"), 24)
                self.assertEqual(kwargs.get("max_sample_rate"), 48000)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_browse_wav_dir_continues_when_save_preferences_fails(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "filedialog.askdirectory": None,
                        "save_preferences": {
                            "side_effect": OSError("permission denied")
                        },
                    },
                )
            ) as mocks:
                ask_dir = mocks["filedialog.askdirectory"]
                ask_dir.return_value = "/tmp/chosen-wav"
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._browse_wav_dir()
                self.assertEqual(app.wav_dir_var.get(), "/tmp/chosen-wav")
                self.assertIn("Couldn’t save preferences", app.status_var.get())
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_start_convert_continues_when_save_preferences_fails(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {
                        "save_preferences": {
                            "side_effect": OSError("permission denied")
                        },
                        "threading.Thread": None,
                    },
                )
            ) as mocks, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ):
                thread_cls = mocks["threading.Thread"]
                thread_cls.return_value.start = lambda: None
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                app.wav_dir_var.set("/tmp/typed-wav")
                mark_output_folder_valid(app)
                seed_track_selection(app)
                app._start_convert()
                self.assertEqual(thread_cls.call_count, 2)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
