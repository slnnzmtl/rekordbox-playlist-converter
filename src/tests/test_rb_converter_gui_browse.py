#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from rb_converter_gui import FALLBACK_WAV_DIR
from update_check import UpdateCheckResult
from gui_tk import (
    app_patches,
    tk_available,
)


class GuiBrowseInitialDirTests(unittest.TestCase):
    def test_browse_xml_uses_home_when_documents_not_accessible(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                **{
                    "filedialog.askopenfilename": None,
                },
            ) as mocks:
                ask_open = mocks["filedialog.askopenfilename"]
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                **{
                    "filedialog.askopenfilename": None,
                },
            ) as mocks:
                ask_open = mocks["filedialog.askopenfilename"]
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        expected_dir = Path("/tmp/saved-exports")
        last_xml = expected_dir / "Rekordbox-collection.xml"
        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                **{
                    "filedialog.askopenfilename": None,
                },
            ) as mocks:
                ask_open = mocks["filedialog.askopenfilename"]
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
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                check_for_update=UpdateCheckResult(kind="up_to_date"),
                load_preferences={},
                **{
                    "filedialog.askdirectory": None,
                    "save_preferences": None,
                },
            ) as mocks:
                ask_dir = mocks["filedialog.askdirectory"]
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
