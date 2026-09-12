#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from update_check import ReleaseInfo, UpdateCheckResult


class UpdateCheckBehaviorTests(unittest.TestCase):
    def test_startup_update_modal_not_shown_twice_in_session(self) -> None:
        try:
            import _tkinter  # noqa: F401
        except ImportError:
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        release = ReleaseInfo(
            version="9.9.9",
            tag_name="v9.9.9",
            html_url="https://example.com/release",
            release_notes="New features",
        )
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="update_available", release=release),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
                with patch.object(app, "_show_update_available") as show_modal:
                    app._handle_update_check_result(
                        UpdateCheckResult(kind="update_available", release=release),
                        manual=False,
                    )
                    app._handle_update_check_result(
                        UpdateCheckResult(kind="update_available", release=release),
                        manual=False,
                    )
                    show_modal.assert_called_once()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_start_update_check_ignores_second_call_while_in_flight(self) -> None:
        """Given an update check already in flight, when _start_update_check is
        called again, then a second Thread is not started."""
        try:
            import _tkinter  # noqa: F401
        except ImportError:
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        started: list[object] = []

        class FakeThread:
            def __init__(self, target=None, daemon=None, **_kwargs):
                self._target = target
                self.daemon = daemon

            def start(self) -> None:
                started.append(self)

            def is_alive(self) -> bool:
                return True

        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]), patch.object(
                ConverterApp, "_start_update_check"
            ):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)

            with patch("rb_converter_gui.threading.Thread", FakeThread):
                started.clear()
                app._start_update_check(manual=True)
                app._start_update_check(manual=True)
                self.assertEqual(
                    len(started),
                    1,
                    "second _start_update_check while first is in flight must be a no-op",
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_view_release_opens_github_page(self) -> None:
        try:
            import _tkinter  # noqa: F401
        except ImportError:
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from rb_converter_gui import ConverterApp

        release = ReleaseInfo(
            version="1.0.1",
            tag_name="v1.0.1",
            html_url="https://github.com/example/release",
            release_notes="Notes",
        )
        root = None
        try:
            with patch(
                "rb_converter_gui.check_for_update",
                return_value=UpdateCheckResult(kind="up_to_date"),
            ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]):
                root = tk.Tk()
                root.withdraw()
                app = ConverterApp(root, documents_accessible=False)
            with patch("rb_converter_gui.webbrowser.open") as open_url, patch.object(
                tk.Toplevel, "wait_window"
            ):
                app._show_update_available(release)
                for child in root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        self._click_button(child, "View release")
                        break
                open_url.assert_called_once_with("https://github.com/example/release")
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
            if UpdateCheckBehaviorTests._click_button(child, label):
                return True
        return False


if __name__ == "__main__":
    unittest.main()
