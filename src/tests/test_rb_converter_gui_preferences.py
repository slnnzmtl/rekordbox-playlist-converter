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

    def test_startup_searches_when_saved_source_xml_missing(self) -> None:
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
            ), patch.object(ConverterApp, "_load_playlists"):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._apply_xml_search_hits([found])
            self.assertEqual(app.xml_var.get(), str(found))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_uses_home_fallback_when_documents_not_accessible(self) -> None:
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
            self.assertEqual(app.wav_dir_var.get(), str(FALLBACK_WAV_DIR))
            self.assertEqual(app.output_var.get(), str(FALLBACK_OUTPUT))
            self.assertFalse(app.documents_accessible)
            self.assertEqual(app.xml_var.get(), "")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_uses_documents_defaults_when_accessible(self) -> None:
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
                app = ConverterApp(root, documents_accessible=True)
            self.assertEqual(app.wav_dir_var.get(), str(DEFAULT_WAV_DIR))
            self.assertEqual(app.output_var.get(), str(DEFAULT_OUTPUT))
            self.assertTrue(app.documents_accessible)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_uses_defaults_when_no_saved_preferences(self) -> None:
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
            ), patch("rb_converter_gui.find_rekordbox_xml_via_child", return_value=[]):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=True)
            self.assertEqual(app.wav_dir_var.get(), str(DEFAULT_WAV_DIR))
            self.assertEqual(app.output_var.get(), str(DEFAULT_OUTPUT))
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
            self.assertEqual(app.xml_var.get(), str(xml_path))
            save_prefs.assert_called()
            self.assertEqual(
                save_prefs.call_args.kwargs.get("source_xml"), xml_path
            )
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
                self.assertEqual(
                    kwargs.get("source_xml"), Path("/tmp/source-rekordbox.xml")
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
                app._start_convert()
                save_prefs.assert_called_once()
                args = save_prefs.call_args[0]
                kwargs = save_prefs.call_args.kwargs
                self.assertEqual(args[0], Path("/tmp/typed-wav"))
                self.assertEqual(args[1], Path("/tmp/typed-import.xml"))
                self.assertEqual(kwargs.get("source_xml"), Path("/tmp/test.xml"))
                self.assertEqual(kwargs.get("output_format"), "aiff")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_format_defaults_to_wav(self) -> None:
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
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                self.assertEqual(app.format_var.get(), "wav")
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


if __name__ == "__main__":
    unittest.main()
