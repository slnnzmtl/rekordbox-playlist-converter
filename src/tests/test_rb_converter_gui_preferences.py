#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR
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
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
            self.assertEqual(app.wav_dir_var.get(), str(saved_wav))
            self.assertEqual(app.output_var.get(), str(saved_xml))
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
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
            self.assertEqual(app.wav_dir_var.get(), str(DEFAULT_WAV_DIR))
            self.assertEqual(app.output_var.get(), str(DEFAULT_OUTPUT))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


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
            ), patch("rb_converter_gui.filedialog.askdirectory") as ask_dir, patch(
                "rb_converter_gui.save_preferences"
            ) as save_prefs:
                ask_dir.return_value = "/tmp/chosen-wav"
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
                app.output_var.set("/tmp/chosen-import.xml")
                app._browse_wav_dir()
                save_prefs.assert_called_once()
                args = save_prefs.call_args[0]
                self.assertEqual(args[0], Path("/tmp/chosen-wav"))
                self.assertEqual(args[1], Path("/tmp/chosen-import.xml"))
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
            ), patch("rb_converter_gui.filedialog.asksaveasfilename") as ask_save, patch(
                "rb_converter_gui.save_preferences"
            ) as save_prefs:
                ask_save.return_value = "/tmp/chosen-import.xml"
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
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
            ), patch("rb_converter_gui.save_preferences") as save_prefs, patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch("rb_converter_gui.threading.Thread") as thread_cls:
                thread_cls.return_value.start = lambda: None
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
                app.xml_var.set("/tmp/test.xml")
                app.wav_dir_var.set("/tmp/typed-wav")
                app.output_var.set("/tmp/typed-import.xml")
                app._start_convert()
                save_prefs.assert_called_once()
                args = save_prefs.call_args[0]
                self.assertEqual(args[0], Path("/tmp/typed-wav"))
                self.assertEqual(args[1], Path("/tmp/typed-import.xml"))
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
            ), patch("rb_converter_gui.filedialog.askdirectory") as ask_dir, patch(
                "rb_converter_gui.save_preferences",
                side_effect=OSError("permission denied"),
            ):
                ask_dir.return_value = "/tmp/chosen-wav"
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
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
            ), patch(
                "rb_converter_gui.save_preferences",
                side_effect=OSError("permission denied"),
            ), patch.object(
                ConverterApp, "_selected_playlists", return_value=[("ROOT", "Test")]
            ), patch("rb_converter_gui.threading.Thread") as thread_cls:
                thread_cls.return_value.start = lambda: None
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root)
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


if __name__ == "__main__":
    unittest.main()
