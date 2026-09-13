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

from update_check import UpdateCheckResult
from gui_tk import (
    app_patches,
    click_button,
    find_listbox,
    merge_patches,
    run_inline_thread,
    startup_patches,
    tk_available,
)

class GuiXmlRefreshTests(unittest.TestCase):
    def test_refresh_reloads_xml_without_browse_dialog(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                merge_patches(
                    startup_patches(),
                    {"filedialog.askopenfilename": None},
                )
            ) as mocks, patch.object(ConverterApp, "_load_playlists") as load:
                ask_open = mocks["filedialog.askopenfilename"]
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                app.xml_var.set("/tmp/rekordbox.xml")
                load.reset_mock()
                self.assertTrue(click_button(root, "Refresh"))
                ask_open.assert_not_called()
                load.assert_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

class GuiFileMenuXmlSearchTests(unittest.TestCase):
    def test_file_menu_search_shows_choice_modal_when_xml_already_loaded(self) -> None:
        if not tk_available():
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
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                find_rekordbox_xml_via_child=hits,
                **{"threading.Thread": {"side_effect": run_inline_thread}},
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
                listbox = find_listbox(dlg)
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        found = Path("/tmp/found/Rekordbox-collection.xml")
        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                find_rekordbox_xml_via_child=[found],
                save_preferences=None,
                **{"threading.Thread": {"side_effect": run_inline_thread}},
            ) as mocks, patch.object(ConverterApp, "_load_playlists"):
                find_xml = mocks["find_rekordbox_xml_via_child"]
                save_prefs = mocks["save_preferences"]
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

if __name__ == "__main__":
    unittest.main()
