#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, FALLBACK_OUTPUT, FALLBACK_WAV_DIR
from update_check import UpdateCheckResult


def _tk_available() -> bool:
    try:
        import _tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def _seed_track_selection(app, folder="ROOT", name="Test", key="1"):
    import tkinter as tk

    leaf = app.tracklist_tree.insert("", tk.END, text="seed")
    app._tracklist_iids[leaf] = (folder, name, key)
    app.tracklist_tree.selection_set(leaf)
    return leaf


def _mark_output_folder_valid(app) -> None:
    """Bypass async folder validation so Convert can run in unit tests."""
    after_id = getattr(app, "_wav_dir_validate_after_id", None)
    if after_id is not None:
        app.root.after_cancel(after_id)
        app._wav_dir_validate_after_id = None
    app._wav_dir_checking = False
    app._wav_dir_valid = True
    app._set_wav_dir_error("")
    app._update_convert_enabled()


class GuiPreferencesStartupTests(unittest.TestCase):
    def test_startup_restores_saved_wav_dir_and_derives_xml(self) -> None:
        """Given saved wav_dir and legacy import_xml: When the app starts: Then
        wav_dir is restored and XML is always <wav_dir>/rekordbox-import.xml."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        saved_wav = Path("/tmp/saved-wav-dir")
        derived_xml = saved_wav / "rekordbox-import.xml"
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch(
                "rb_converter_gui.load_preferences",
                return_value={
                    "wav_dir": str(saved_wav),
                    "import_xml": str(saved_wav / "custom-import.xml"),
                },
            ), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(saved_wav, derived_xml),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
            self.assertEqual(app.wav_dir_var.get(), str(saved_wav))
            self.assertEqual(app._resolved_output_paths()[1], derived_xml)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_restores_saved_source_xml_before_search(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tempfile
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "Rekordbox-collection.xml"
                source.write_text(
                    '<?xml version="1.0"?><DJ_PLAYLISTS Version="1.0.0">'
                    "<PRODUCT Name='x' Version='1' Company='x'/>"
                    "<COLLECTION Entries='0'/>"
                    "<PLAYLISTS><NODE Type='0' Name='ROOT' Count='0'/></PLAYLISTS>"
                    "</DJ_PLAYLISTS>",
                    encoding="utf-8",
                )
                other = Path("/tmp/other-rekordbox.xml")
                with patch(
                    "rb_converter_gui.check_for_update",
                    return_value=UpdateCheckResult(kind="up_to_date"),
                ), patch(
                    "rb_converter_gui.load_preferences",
                    return_value={"source_xml": str(source)},
                ), patch(
                    "rb_converter_gui.find_rekordbox_xml_via_child",
                    return_value=[other],
                ), patch(
                    "rb_converter_gui.probe_path_via_child", return_value=False
                ), patch.object(ConverterApp, "_load_playlists") as load_playlists:
                    root = tk.Tk()
                    root.withdraw()
                    app = ConverterApp(root)
                    app._probe_documents_after_idle()
                self.assertEqual(app.xml_var.get(), str(source))
                load_playlists.assert_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_skips_xml_search_when_saved_source_xml_file_missing(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        missing = Path("/tmp/does-not-exist-rekordbox.xml")
        found = Path("/tmp/found-rekordbox.xml")
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch(
                "rb_converter_gui.load_preferences",
                return_value={"source_xml": str(missing)},
            ), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child",
                return_value=[found],
            ) as finder, patch(
                "rb_converter_gui.probe_path_via_child", return_value=False
            ), patch.object(ConverterApp, "_load_playlists"):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
                app._probe_documents_after_idle()
            finder.assert_not_called()
            self.assertEqual(app.xml_var.get(), "")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_skips_documents_source_xml_until_access_granted(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tempfile
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "Documents" / "Rekordbox-collection.xml"
                source.parent.mkdir(parents=True)
                source.write_text(
                    '<?xml version="1.0"?><DJ_PLAYLISTS Version="1.0.0">'
                    "<PRODUCT Name='x' Version='1' Company='x'/>"
                    "<COLLECTION Entries='0'/>"
                    "<PLAYLISTS><NODE Type='0' Name='ROOT' Count='0'/></PLAYLISTS>"
                    "</DJ_PLAYLISTS>",
                    encoding="utf-8",
                )

                with patch(
                    "rb_converter_gui.check_for_update",
                    return_value=UpdateCheckResult(kind="up_to_date"),
                ), patch(
                    "rb_converter_gui.load_preferences",
                    return_value={"source_xml": str(source)},
                ), patch(
                    "rb_converter_gui.rb.path_is_under_documents",
                    return_value=True,
                ), patch(
                    "rb_converter_gui.find_rekordbox_xml_via_child",
                    return_value=[],
                ), patch.object(ConverterApp, "_load_playlists") as load_playlists:
                    root = tk.Tk()
                    root.withdraw()
                    app = ConverterApp(root, documents_accessible=False)
                    self.assertFalse(app.xml_var.get().strip())
                    load_playlists.assert_not_called()
                    app._apply_documents_access(True)
                    self.assertEqual(Path(app.xml_var.get()), source)
                    load_playlists.assert_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_skips_xml_search_when_source_xml_already_saved(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        saved = Path("/tmp/Documents/rekordbox/Playlists/rekordbox-7-collection.xml")
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch(
                "rb_converter_gui.load_preferences",
                return_value={"source_xml": str(saved)},
            ), patch(
                "rb_converter_gui.rb.path_is_under_documents",
                return_value=True,
            ), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child",
                return_value=[
                    saved,
                    Path("/tmp/Documents/rekordbox/rekordbox-7-collection.xml"),
                ],
            ) as finder, patch.object(ConverterApp, "_load_playlists"):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._probe_documents_after_idle()
                finder.assert_not_called()
                dialogs = [
                    child
                    for child in root.winfo_children()
                    if isinstance(child, tk.Toplevel)
                ]
                self.assertEqual(dialogs, [])
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_does_not_probe_documents_before_window_is_shown(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.probe_path_via_child"
            ) as probe, patch(
                "rb_converter_gui.find_rekordbox_xml_via_child", return_value=[]
            ) as finder:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
            self.assertEqual(app.wav_dir_var.get(), str(FALLBACK_WAV_DIR))
            self.assertEqual(
                app._resolved_output_paths()[1], FALLBACK_OUTPUT
            )
            self.assertFalse(app.documents_accessible)
            probe.assert_not_called()
            finder.assert_not_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_idle_documents_probe_upgrades_paths_when_child_succeeds(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child", return_value=[]
            ), patch("rb_converter_gui.probe_path_via_child", return_value=True):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
                app._probe_documents_after_idle()
            self.assertTrue(app.documents_accessible)
            self.assertEqual(app.wav_dir_var.get(), str(DEFAULT_WAV_DIR))
            self.assertEqual(app._resolved_output_paths()[1], DEFAULT_OUTPUT)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_idle_documents_probe_keeps_home_fallback_when_child_times_out(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child", return_value=[]
            ), patch("rb_converter_gui.probe_path_via_child", return_value=False):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
                app._probe_documents_after_idle()
            self.assertFalse(app.documents_accessible)
            self.assertEqual(app.wav_dir_var.get(), str(FALLBACK_WAV_DIR))
            self.assertEqual(
                app._resolved_output_paths()[1], FALLBACK_OUTPUT
            )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_home_xml_search_autoloads_single_hit_when_folder_listing_denied(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        xml_path = Path("/tmp/Rekordbox-collection.xml")
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child",
                return_value=[xml_path],
            ), patch.object(ConverterApp, "_load_playlists"), patch(
                "rb_converter_gui.save_preferences"
            ) as save_prefs:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._apply_xml_search_hits([xml_path])
            self.assertFalse(app.documents_accessible)
            self.assertEqual(Path(app.xml_var.get()), xml_path)
            save_prefs.assert_not_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_home_xml_search_leaves_empty_when_zero_hits(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child", return_value=[]
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._apply_xml_search_hits([])
            self.assertEqual(app.xml_var.get(), "")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_home_xml_search_modal_open_loads_selected_path(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        paths = [
            Path("/tmp/a/rekordbox.xml"),
            Path("/tmp/b/Rekordbox-collection.xml"),
        ]
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child", return_value=[]
            ), patch.object(ConverterApp, "_load_playlists"), patch.object(
                tk.Toplevel, "wait_window"
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._apply_xml_search_hits(paths)
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                listbox = self._find_listbox(dlg)
                self.assertIsNotNone(listbox)
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(1)
                with patch("rb_converter_gui.save_preferences") as save_prefs:
                    self.assertTrue(self._click_button(dlg, "Open"))
                    self.assertEqual(app.xml_var.get(), str(paths[1]))
                    save_prefs.assert_called()
                    self.assertEqual(
                        save_prefs.call_args.kwargs.get("source_xml"), paths[1]
                    )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_home_xml_search_modal_cancel_leaves_empty(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        paths = [
            Path("/tmp/a/rekordbox.xml"),
            Path("/tmp/b/Rekordbox-collection.xml"),
        ]
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child", return_value=[]
            ), patch.object(tk.Toplevel, "wait_window"):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._apply_xml_search_hits(paths)
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                self.assertTrue(self._click_button(dlg, "Cancel"))
            self.assertEqual(app.xml_var.get(), "")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    @staticmethod
    def _click_button(widget, label: str) -> bool:
        import tkinter as tk
        from tkinter import ttk

        if isinstance(widget, ttk.Button) and widget.cget("text") == label:
            widget.invoke()
            return True
        for child in widget.winfo_children():
            if GuiPreferencesStartupTests._click_button(child, label):
                return True
        return False

    @staticmethod
    def _find_listbox(widget):
        import tkinter as tk

        if isinstance(widget, tk.Listbox):
            return widget
        for child in widget.winfo_children():
            found = GuiPreferencesStartupTests._find_listbox(child)
            if found is not None:
                return found
        return None


class GuiPreferencesPersistTests(unittest.TestCase):
    def test_sampling_combobox_change_persists_preferences(self) -> None:
        """Given ConverterApp, when bit depth / sample rate combobox handlers run,
        then save_preferences is called with the newly selected values."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch(
                "rb_converter_gui.save_preferences"
            ) as save_prefs:
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
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch(
                "rb_converter_gui.filedialog.askdirectory"
            ) as ask_dir, patch("rb_converter_gui.save_preferences") as save_prefs:
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
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch(
                "rb_converter_gui.filedialog.askopenfilename"
            ) as ask_open, patch(
                "rb_converter_gui.save_preferences"
            ) as save_prefs, patch.object(ConverterApp, "_load_playlists"):
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
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch(
                "rb_converter_gui.save_preferences"
            ):
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
            # Expire the temporary status the same way the 3s after() would.
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
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch(
                "rb_converter_gui.save_preferences"
            ):
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
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch(
                "rb_converter_gui.save_preferences"
            ) as save_prefs, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch("rb_converter_gui.threading.Thread") as thread_cls:
                thread_cls.return_value.start = lambda: None
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                app.wav_dir_var.set("/tmp/typed-wav")
                app.format_var.set("aiff")
                app.bit_depth_var.set("24")
                app.sample_rate_var.set("48000")
                _mark_output_folder_valid(app)
                _seed_track_selection(app)
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
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch(
                "rb_converter_gui.load_preferences",
                return_value={
                    "output_format": "aiff",
                    "bit_depth": "24",
                    "sample_rate": "48000",
                },
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
                "rb_converter_gui.rb.prepare", return_value=(None, ["stop"])
            ) as prepare, patch(
                "rb_converter_gui.threading.Thread"
            ) as thread_cls:

                def capture_start():
                    # Run worker synchronously for assertions.
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
                _seed_track_selection(app)
                _mark_output_folder_valid(app)
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
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch(
                "rb_converter_gui.filedialog.askdirectory"
            ) as ask_dir, patch(
                "rb_converter_gui.save_preferences",
                side_effect=OSError("permission denied"),
            ):
                ask_dir.return_value = "/tmp/chosen-wav"
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._browse_wav_dir()
                self.assertEqual(app.wav_dir_var.get(), "/tmp/chosen-wav")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_start_convert_continues_when_save_preferences_fails(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch(
                "rb_converter_gui.save_preferences",
                side_effect=OSError("permission denied"),
            ), patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch("rb_converter_gui.threading.Thread") as thread_cls:
                thread_cls.return_value.start = lambda: None
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/test.xml")
                app.wav_dir_var.set("/tmp/typed-wav")
                _mark_output_folder_valid(app)
                _seed_track_selection(app)
                app._start_convert()
                self.assertEqual(thread_cls.call_count, 2)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


class GuiBrowseInitialDirTests(unittest.TestCase):
    def test_browse_xml_uses_home_when_documents_not_accessible(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.rb.discover_xml_candidates", return_value=[]
            ), patch("rb_converter_gui.filedialog.askopenfilename") as ask_open:
                ask_open.return_value = ""
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._browse_xml()
                kwargs = ask_open.call_args.kwargs
                self.assertEqual(kwargs["initialdir"], str(Path.home()))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_browse_xml_uses_documents_when_accessible(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.rb.discover_xml_candidates", return_value=[]
            ), patch("rb_converter_gui.filedialog.askopenfilename") as ask_open:
                ask_open.return_value = ""
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=True)
                app._browse_xml()
                kwargs = ask_open.call_args.kwargs
                self.assertEqual(
                    kwargs["initialdir"], str(Path.home() / "Documents")
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_browse_xml_uses_last_xml_parent_as_initialdir(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        expected_dir = Path("/tmp/saved-exports")
        last_xml = expected_dir / "Rekordbox-collection.xml"
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.rb.discover_xml_candidates", return_value=[]
            ), patch("rb_converter_gui.filedialog.askopenfilename") as ask_open:
                ask_open.return_value = ""
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set(str(last_xml))
                app._browse_xml()
                kwargs = ask_open.call_args.kwargs
                self.assertEqual(Path(kwargs["initialdir"]), expected_dir)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_browse_wav_dir_uses_home_fallback_when_documents_not_accessible(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.rb.discover_xml_candidates", return_value=[]
            ), patch("rb_converter_gui.filedialog.askdirectory") as ask_dir, patch(
                "rb_converter_gui.save_preferences"
            ):
                ask_dir.return_value = ""
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._browse_wav_dir()
                kwargs = ask_dir.call_args.kwargs
                self.assertEqual(kwargs["initialdir"], str(FALLBACK_WAV_DIR))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


class GuiXmlRefreshTests(unittest.TestCase):
    def test_refresh_reloads_xml_without_browse_dialog(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch(
                "rb_converter_gui.rb.discover_xml_candidates", return_value=[]
            ), patch(
                "rb_converter_gui.filedialog.askopenfilename"
            ) as ask_open, patch.object(ConverterApp, "_load_playlists") as load:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/rekordbox.xml")
                load.reset_mock()
                self.assertTrue(
                    GuiPreferencesStartupTests._click_button(root, "Refresh")
                )
                ask_open.assert_not_called()
                load.assert_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


class GuiFileMenuXmlSearchTests(unittest.TestCase):
    @staticmethod
    def _run_inline_thread(target=None, daemon=None, **_kwargs):
        class _T:
            def start(self_inner):
                if target is not None:
                    target()

            def is_alive(self_inner):
                return False

            def join(self_inner, timeout=None):
                return None

        return _T()

    def test_file_menu_search_shows_choice_modal_when_xml_already_loaded(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        current = Path("/tmp/already-loaded/rekordbox.xml")
        hits = [
            Path("/tmp/a/rekordbox.xml"),
            Path("/tmp/b/Rekordbox-collection.xml"),
        ]
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child",
                return_value=hits,
            ), patch(
                "rb_converter_gui.threading.Thread",
                side_effect=self._run_inline_thread,
            ), patch.object(ConverterApp, "_load_playlists"), patch.object(
                tk.Toplevel, "wait_window"
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set(str(current))
                self.assertTrue(
                    self._invoke_menu(root, "File", "Search for Rekordbox XML…")
                )
                root.update()
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                listbox = GuiPreferencesStartupTests._find_listbox(dlg)
                self.assertIsNotNone(listbox)
                self.assertEqual(
                    [Path(listbox.get(i)) for i in range(listbox.size())],
                    hits,
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_file_menu_search_single_hit_loads_and_remembers_path(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        found = Path("/tmp/found/Rekordbox-collection.xml")
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.find_rekordbox_xml_via_child",
                return_value=[found],
            ) as find_xml, patch(
                "rb_converter_gui.threading.Thread",
                side_effect=self._run_inline_thread,
            ), patch.object(ConverterApp, "_load_playlists"), patch(
                "rb_converter_gui.save_preferences"
            ) as save_prefs:
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/already-loaded/rekordbox.xml")
                self.assertTrue(
                    self._invoke_menu(root, "File", "Search for Rekordbox XML…")
                )
                root.update()
                self.assertEqual(Path(app.xml_var.get()), found)
                self.assertGreater(
                    find_xml.call_args.kwargs.get("timeout_seconds") or 0, 0
                )
                self.assertEqual(
                    save_prefs.call_args.kwargs.get("source_xml"), found
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    @staticmethod
    def _invoke_menu(root, cascade: str, item: str) -> bool:
        menubar_path = root.cget("menu")
        if not menubar_path:
            return False
        menubar = root.nametowidget(menubar_path)
        end = menubar.index("end")
        if end is None:
            return False
        for i in range(end + 1):
            if menubar.type(i) != "cascade":
                continue
            if menubar.entrycget(i, "label") != cascade:
                continue
            submenu = menubar.nametowidget(menubar.entrycget(i, "menu"))
            sub_end = submenu.index("end")
            if sub_end is None:
                return False
            for j in range(sub_end + 1):
                if submenu.type(j) != "command":
                    continue
                if submenu.entrycget(j, "label") == item:
                    submenu.invoke(j)
                    return True
        return False


class GuiLibraryValidationTests(unittest.TestCase):
    def test_validation_error_disables_convert(self) -> None:
        """Given a legacy folder without a manifest: When validation finishes:
        Then Convert is disabled and an inline error is shown."""
        if not _tk_available():
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
                with patch(
                    "rb_converter_gui.check_for_update",
                    return_value=UpdateCheckResult(kind="up_to_date"),
                ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                    "rb_converter_gui.resolve_startup_paths",
                    return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
                ), patch(
                    "rb_converter_gui.rb.discover_xml_candidates", return_value=[]
                ), patch("rb_converter_gui.save_preferences"):
                    root = tk.Tk()
                    root.withdraw()
                    app = ConverterApp(root, documents_accessible=False)
                    app.wav_dir_var.set(str(legacy))
                    # Run validation inline (no debounce / worker).
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
                    self.assertIn("new empty output folder", app.wav_dir_error_var.get().lower())
                    self.assertTrue(app.wav_dir_error_label.winfo_manager())

                    empty = Path(tmp) / "empty-lib"
                    empty.mkdir()
                    app.wav_dir_var.set(str(empty))
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
        opens the WAV or AIFF playlist_dir, and Reveal import XML opens the
        generated XML."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.load_preferences", return_value={}), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
            ), patch(
                "rb_converter_gui.rb.discover_xml_candidates", return_value=[]
            ), patch.object(tk.Toplevel, "wait_window"), patch(
                "rb_converter_gui.open_in_finder"
            ) as reveal:
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
                self.assertTrue(
                    GuiPreferencesStartupTests._click_button(
                        dlg, "Reveal audio folder"
                    )
                )
                reveal.assert_called_with(wav_folder)

                app._show_done_dialog("Done body", aiff_folder, xml)
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                self.assertTrue(
                    GuiPreferencesStartupTests._click_button(
                        dlg, "Reveal audio folder"
                    )
                )
                reveal.assert_called_with(aiff_folder)

                app._show_done_dialog("Done body", wav_folder, xml)
                dlg = None
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        dlg = child
                        break
                self.assertIsNotNone(dlg)
                self.assertTrue(
                    GuiPreferencesStartupTests._click_button(dlg, "Reveal import XML")
                )
                reveal.assert_called_with(xml)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
