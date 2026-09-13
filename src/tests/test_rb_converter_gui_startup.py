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

from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, FALLBACK_OUTPUT, FALLBACK_WAV_DIR
from update_check import UpdateCheckResult
from gui.startup_probe import run_startup_probe
import gui.runtime as runtime
from gui_tk import (
    app_patches,
    click_button,
    find_listbox,
    patch_gui,
    startup_patches,
    tk_available,
)


class GuiPreferencesStartupTests(unittest.TestCase):
    def test_startup_restores_saved_wav_dir_and_derives_xml(self) -> None:
        """Given saved wav_dir and legacy import_xml: When the app starts: Then
        wav_dir is restored and XML is always <wav_dir>/rekordbox-import.xml."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        saved_wav = Path("/tmp/saved-wav-dir")
        derived_xml = saved_wav / "rekordbox-import.xml"
        root = None
        try:
            with app_patches(
                **startup_patches(
                    wav_dir=saved_wav,
                    output=derived_xml,
                    preferences={
                        "library_dir": str(saved_wav),
                        "import_xml": str(saved_wav / "custom-import.xml"),
                    },
                )
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
            self.assertEqual(app.library_dir_var.get(), str(saved_wav))
            self.assertEqual(app._resolved_output_paths()[1], derived_xml)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_restores_saved_source_xml_before_search(self) -> None:
        if not tk_available():
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
                with app_patches(
                    check_for_update=UpdateCheckResult(kind="up_to_date"),
                    load_preferences={"source_xml": str(source)},
                    find_rekordbox_xml_via_child=[other],
                    probe_path_via_child=False,
                ), patch.object(ConverterApp, "_load_playlists") as load_playlists:
                    root = tk.Tk()
                    root.withdraw()
                    app = ConverterApp(root)
                    app._apply_startup_probe(run_startup_probe(
                    has_saved_source_xml=app._has_saved_source_xml,
                    probe_documents=runtime.probe_path_via_child,
                    find_xml=runtime.find_rekordbox_xml_via_child,
                ))
                self.assertEqual(app.xml_var.get(), str(source))
                load_playlists.assert_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_skips_xml_search_when_saved_source_xml_file_missing(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        missing = Path("/tmp/does-not-exist-rekordbox.xml")
        found = Path("/tmp/found-rekordbox.xml")
        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={"source_xml": str(missing)},
                find_rekordbox_xml_via_child=[found],
                probe_path_via_child=False,
            ) as mocks, patch.object(ConverterApp, "_load_playlists"):
                finder = mocks["find_rekordbox_xml_via_child"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
                app._apply_startup_probe(run_startup_probe(
                    has_saved_source_xml=app._has_saved_source_xml,
                    probe_documents=runtime.probe_path_via_child,
                    find_xml=runtime.find_rekordbox_xml_via_child,
                ))
            finder.assert_not_called()
            self.assertEqual(app.xml_var.get(), "")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_startup_skips_documents_source_xml_until_access_granted(self) -> None:
        if not tk_available():
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

                with app_patches(
                    check_for_update=UpdateCheckResult(kind="up_to_date"),
                    load_preferences={"source_xml": str(source)},
                    **{
                        "path_is_under_documents": True,
                        "find_rekordbox_xml_via_child": [],
                    },
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        saved = Path("/tmp/Documents/rekordbox/Playlists/rekordbox-7-collection.xml")
        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={"source_xml": str(saved)},
                **{
                    "path_is_under_documents": True,
                    "find_rekordbox_xml_via_child": [
                        saved,
                        Path("/tmp/Documents/rekordbox/rekordbox-7-collection.xml"),
                    ],
                },
            ) as mocks, patch.object(ConverterApp, "_load_playlists"):
                finder = mocks["find_rekordbox_xml_via_child"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app._apply_startup_probe(run_startup_probe(
                    has_saved_source_xml=app._has_saved_source_xml,
                    probe_documents=runtime.probe_path_via_child,
                    find_xml=runtime.find_rekordbox_xml_via_child,
                ))
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                probe_path_via_child=None,
                find_rekordbox_xml_via_child=[],
            ) as mocks:
                probe = mocks["probe_path_via_child"]
                finder = mocks["find_rekordbox_xml_via_child"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
            self.assertEqual(app.library_dir_var.get(), str(FALLBACK_WAV_DIR))
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                find_rekordbox_xml_via_child=[],
                probe_path_via_child=True,
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
                app._apply_startup_probe(run_startup_probe(
                    has_saved_source_xml=app._has_saved_source_xml,
                    probe_documents=runtime.probe_path_via_child,
                    find_xml=runtime.find_rekordbox_xml_via_child,
                ))
            self.assertTrue(app.documents_accessible)
            self.assertEqual(app.library_dir_var.get(), str(DEFAULT_WAV_DIR))
            self.assertEqual(app._resolved_output_paths()[1], DEFAULT_OUTPUT)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_idle_documents_probe_keeps_home_fallback_when_child_times_out(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                find_rekordbox_xml_via_child=[],
                probe_path_via_child=False,
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
                app._apply_startup_probe(run_startup_probe(
                    has_saved_source_xml=app._has_saved_source_xml,
                    probe_documents=runtime.probe_path_via_child,
                    find_xml=runtime.find_rekordbox_xml_via_child,
                ))
            self.assertFalse(app.documents_accessible)
            self.assertEqual(app.library_dir_var.get(), str(FALLBACK_WAV_DIR))
            self.assertEqual(
                app._resolved_output_paths()[1], FALLBACK_OUTPUT
            )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_home_xml_search_autoloads_single_hit_when_folder_listing_denied(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        xml_path = Path("/tmp/Rekordbox-collection.xml")
        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                find_rekordbox_xml_via_child=[xml_path],
                save_preferences=None,
            ) as mocks, patch.object(ConverterApp, "_load_playlists"):
                save_prefs = mocks["save_preferences"]
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                find_rekordbox_xml_via_child=[],
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        paths = [
            Path("/tmp/a/rekordbox.xml"),
            Path("/tmp/b/Rekordbox-collection.xml"),
        ]
        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                find_rekordbox_xml_via_child=[],
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
                listbox = find_listbox(dlg)
                self.assertIsNotNone(listbox)
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(1)
                with patch_gui("save_preferences") as save_prefs:
                    self.assertTrue(click_button(dlg, "Open"))
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        paths = [
            Path("/tmp/a/rekordbox.xml"),
            Path("/tmp/b/Rekordbox-collection.xml"),
        ]
        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                find_rekordbox_xml_via_child=[],
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
                self.assertTrue(click_button(dlg, "Cancel"))
            self.assertEqual(app.xml_var.get(), "")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
