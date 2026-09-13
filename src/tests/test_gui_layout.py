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

from gui_tk import tk_available

from gui.layout import (
    bind_wraplength,
    center_over_window_geometry,
    fit_window_geometry,
    place_dialog_over_parent,
)


class FitWindowGeometryTests(unittest.TestCase):
    def test_fit_window_geometry_centers_inside_nonzero_origin_rect(self) -> None:
        # Secondary-display-style rect: not at virtual (0,0).
        # 1440x900 usable; 1120x720 centered at +2080+115.
        geom = fit_window_geometry(1120, 720, 1920, 25, 3360, 925)
        self.assertEqual(geom, "1120x720+2080+115")


class CenterOverWindowGeometryTests(unittest.TestCase):
    def test_center_over_window_geometry_centers_dialog_on_parent(self) -> None:
        # Parent not at (0,0); child 500x200 over 1120x720 at +400+240.
        geom = center_over_window_geometry(400, 240, 1120, 720, 500, 200)
        self.assertEqual(geom, "+710+500")


class PlaceDialogOverParentTests(unittest.TestCase):
    def test_place_dialog_over_parent_centers_explicit_size(self) -> None:
        """Given a parent at a known origin: When placing a 400x200 dialog:
        Then it is centered on that parent, not on a 1x1 unmapped size."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        dlg = None
        try:
            root = tk.Tk()
            root.geometry("800x600+100+80")
            root.update_idletasks()
            root.update()
            dlg = tk.Toplevel(root)
            dlg.geometry("400x200")
            place_dialog_over_parent(dlg, root)
            root.update_idletasks()
            root.update()
            expected = "400x200" + center_over_window_geometry(
                root.winfo_rootx(),
                root.winfo_rooty(),
                root.winfo_width(),
                root.winfo_height(),
                400,
                200,
            )
            self.assertEqual(dlg.geometry(), expected)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if dlg is not None:
                dlg.destroy()
            if root is not None:
                root.destroy()


class BindWraplengthTests(unittest.TestCase):
    def test_bind_wraplength_updates_label_when_container_resizes(self) -> None:
        """Given a bound label: When the container width changes: Then wraplength follows."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from tkinter import ttk

        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        try:
            root.geometry("600x200")
            container = ttk.Frame(root)
            container.place(x=0, y=0, width=300, height=80)
            label = ttk.Label(container, text="wrap me")
            label.place(x=0, y=0, relwidth=1)
            bind_wraplength(label, container, inset=20)
            root.update_idletasks()
            root.update()

            container.event_generate("<Configure>", width=300, height=80)
            root.update_idletasks()
            root.update()
            self.assertEqual(int(label.cget("wraplength")), 280)

            container.place_configure(width=500)
            container.event_generate("<Configure>", width=500, height=80)
            root.update_idletasks()
            root.update()
            self.assertEqual(int(label.cget("wraplength")), 480)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
