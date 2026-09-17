#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_SRC, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from gui_tk import app_patches, startup_patches, tk_available


def _pump_welcome(root) -> None:
    """Let the deferred welcome after(100) fire without blocking on a dialog."""
    root.update_idletasks()
    root.update()
    root.after(150, root.quit)
    root.mainloop()


class GuiWelcomeTests(unittest.TestCase):
    def test_first_launch_schedules_welcome_dialog(self) -> None:
        """Given no preferences file: When startup deferral runs: Then
        show_welcome_dialog is called once."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(prefs_file_exists=False),
            ), patch(
                "gui.dialogs.show_welcome_dialog"
            ) as show_welcome:
                root = tk.Tk()
                ConverterApp(root, documents_accessible=False)
                _pump_welcome(root)
                show_welcome.assert_called_once()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass

    def test_existing_prefs_skips_welcome_dialog(self) -> None:
        """Given preferences file exists: When startup deferral would run: Then
        show_welcome_dialog is not called."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(prefs_file_exists=True),
            ), patch(
                "gui.dialogs.show_welcome_dialog"
            ) as show_welcome:
                root = tk.Tk()
                ConverterApp(root, documents_accessible=False)
                _pump_welcome(root)
                show_welcome.assert_not_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass

    def test_complete_first_launch_opt_out_saves_analytics_off(self) -> None:
        """Given first launch: When continue with analytics off: Then
        save_preferences(analytics=off) and update check starts."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(prefs_file_exists=False),
                save_preferences=None,
                enable_analytics=None,
            ) as mocks, patch(
                "gui.dialogs.show_welcome_dialog",
                side_effect=lambda *_a, on_result, **_k: on_result(
                    "continue", False
                ),
            ), patch.object(
                ConverterApp, "_start_update_check"
            ) as start_update:
                root = tk.Tk()
                ConverterApp(root, documents_accessible=False)
                _pump_welcome(root)
                mocks["save_preferences"].assert_called_once_with(analytics="off")
                mocks["enable_analytics"].assert_not_called()
                start_update.assert_called_with(manual=False)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass

    def test_complete_first_launch_opt_in_enables_analytics(self) -> None:
        """Given first launch: When continue with analytics on: Then
        enable_analytics(surface=gui) is called."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(prefs_file_exists=False),
                save_preferences=None,
                enable_analytics=None,
                disable_analytics=None,
            ) as mocks, patch(
                "gui.dialogs.show_welcome_dialog",
                side_effect=lambda *_a, on_result, **_k: on_result(
                    "continue", True
                ),
            ):
                root = tk.Tk()
                app = ConverterApp(root, documents_accessible=False)
                _pump_welcome(root)
                mocks["enable_analytics"].assert_called_once_with(surface="gui")
                mocks["disable_analytics"].assert_not_called()
                self.assertTrue(app.analytics_var.get())
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass

    def test_open_full_guide_defers_documents_probe_until_guide_closes(self) -> None:
        """Given first launch: When Open full guide: Then the documents probe
        waits until the usage guide is closed."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            closed_holder: list = []
            with app_patches(
                **startup_patches(prefs_file_exists=False),
                save_preferences=None,
            ) as mocks, patch(
                "gui.dialogs.show_welcome_dialog",
                side_effect=lambda *_a, on_result, **_k: on_result("guide", False),
            ), patch(
                "gui.dialogs.show_usage_guide_dialog",
                side_effect=lambda *_a, on_closed, **_k: closed_holder.append(
                    on_closed
                )
                or object(),
            ), patch.object(
                ConverterApp, "_start_documents_probe"
            ) as start_probe:
                root = tk.Tk()
                app = ConverterApp(root)
                _pump_welcome(root)
                mocks["save_preferences"].assert_called_once_with(analytics="off")
                self.assertFalse(app._startup_done)
                start_probe.assert_not_called()
                self.assertEqual(len(closed_holder), 1)
                closed_holder[0]()
                root.update_idletasks()
                root.update()
                start_probe.assert_called_once()
                self.assertTrue(app._startup_done)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass

    def test_open_full_guide_completes_and_opens_usage(self) -> None:
        """Given first launch: When Open full guide: Then analytics prefs are
        saved and the usage guide opens without releasing startup yet."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(prefs_file_exists=False),
                save_preferences=None,
            ) as mocks, patch(
                "gui.dialogs.show_welcome_dialog",
                side_effect=lambda *_a, on_result, **_k: on_result("guide", False),
            ), patch.object(ConverterApp, "_show_usage_guide") as show_guide:
                root = tk.Tk()
                app = ConverterApp(root, documents_accessible=False)
                _pump_welcome(root)
                mocks["save_preferences"].assert_called_once_with(analytics="off")
                show_guide.assert_called_once()
                self.assertFalse(app._startup_done)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass

    def test_documents_probe_waits_until_welcome_closes(self) -> None:
        """Given first launch: When welcome is still open: Then the documents
        probe has not started; after continue it starts."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        root = None
        try:
            with app_patches(
                **startup_patches(prefs_file_exists=False),
                save_preferences=None,
            ), patch(
                "gui.dialogs.show_welcome_dialog",
                side_effect=lambda *_a, on_result, **_k: on_result(
                    "continue", False
                ),
            ), patch.object(
                ConverterApp, "_start_documents_probe"
            ) as start_probe:
                root = tk.Tk()
                app = ConverterApp(root)  # documents_accessible=None → probe path
                self.assertFalse(app._startup_done)
                start_probe.assert_not_called()
                _pump_welcome(root)
                start_probe.assert_called_once()
                self.assertTrue(app._startup_done)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass


if __name__ == "__main__":
    unittest.main()
