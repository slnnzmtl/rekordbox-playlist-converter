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


class GuiPreferencesStartupTests(unittest.TestCase):
    def test_startup_restores_saved_output_paths(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        saved_wav = Path("/tmp/saved-wav-dir")
        saved_xml = Path("/tmp/saved-wav-dir/custom-import.xml")
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch(
                "rb_converter_gui.load_preferences",
                return_value={
                    "wav_dir": str(saved_wav),
                    "import_xml": str(saved_xml),
                },
            ), patch(
                "rb_converter_gui.resolve_startup_paths",
                return_value=(saved_wav, saved_xml),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
            self.assertEqual(app.wav_dir_var.get(), str(saved_wav))
            self.assertEqual(app.output_var.get(), str(saved_xml))
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
            self.assertEqual(app.output_var.get(), str(FALLBACK_OUTPUT))
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
            self.assertEqual(app.output_var.get(), str(DEFAULT_OUTPUT))
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
            self.assertEqual(app.output_var.get(), str(FALLBACK_OUTPUT))
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
                app.output_var.set("/tmp/chosen-import.xml")
                app.xml_var.set("/tmp/source-rekordbox.xml")
                app._browse_wav_dir()
                save_prefs.assert_called_once()
                args = save_prefs.call_args[0]
                kwargs = save_prefs.call_args.kwargs
                self.assertEqual(args[0], Path("/tmp/chosen-wav"))
                self.assertEqual(args[1], Path("/tmp/chosen-import.xml"))
                self.assertIsNone(kwargs.get("source_xml"))
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

    def test_browse_output_saves_preferences(self) -> None:
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
                "rb_converter_gui.filedialog.asksaveasfilename"
            ) as ask_save, patch("rb_converter_gui.save_preferences") as save_prefs:
                ask_save.return_value = "/tmp/chosen-import.xml"
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.wav_dir_var.set("/tmp/chosen-wav")
                app._browse_output()
                save_prefs.assert_called_once()
                args = save_prefs.call_args[0]
                self.assertEqual(args[0], Path("/tmp/chosen-wav"))
                self.assertEqual(args[1], Path("/tmp/chosen-import.xml"))
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
                app.output_var.set("/tmp/typed-import.xml")
                app.format_var.set("aiff")
                app.bit_depth_var.set("24")
                app.sample_rate_var.set("48000")
                app._start_convert()
                save_prefs.assert_called_once()
                args = save_prefs.call_args[0]
                kwargs = save_prefs.call_args.kwargs
                self.assertEqual(args[0], Path("/tmp/typed-wav"))
                self.assertEqual(args[1], Path("/tmp/typed-import.xml"))
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
                app.output_var.set("/tmp/typed-import.xml")
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


if __name__ == "__main__":
    unittest.main()
