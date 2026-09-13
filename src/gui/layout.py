"""Shared Tk layout helpers for Simple Rekordbox Converter."""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from tkinter import ttk

ACTION_BUTTON_WIDTH = 9


def fit_window_geometry(
    width: int,
    height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> str:
    """Return WxH+X+Y centered and fully inside the given display rect."""
    avail_w = max(1, right - left)
    avail_h = max(1, bottom - top)
    w = min(width, avail_w)
    h = min(height, avail_h)
    x = left + (avail_w - w) // 2
    y = top + (avail_h - h) // 2
    return f"{w}x{h}+{x}+{y}"


def center_over_window_geometry(
    parent_x: int,
    parent_y: int,
    parent_w: int,
    parent_h: int,
    child_w: int,
    child_h: int,
) -> str:
    """Return +X+Y that centers a child window over its parent."""
    x = parent_x + (parent_w - child_w) // 2
    y = parent_y + (parent_h - child_h) // 2
    return f"+{x}+{y}"


def active_display_bounds() -> tuple[int, int, int, int] | None:
    """Usable bounds of the display under the pointer (Tk left, top, right, bottom)."""
    if sys.platform != "darwin":
        return None
    script = (
        "ObjC.import('AppKit');\n"
        "var screens = $.NSScreen.screens;\n"
        "var primary = screens.objectAtIndex(0);\n"
        "var ph = primary.frame.size.height;\n"
        "var mouse = $.NSEvent.mouseLocation;\n"
        "var chosen = primary;\n"
        "for (var i = 0; i < screens.count; i++) {\n"
        "  var s = screens.objectAtIndex(i);\n"
        "  var f = s.frame;\n"
        "  if (mouse.x >= f.origin.x && mouse.x <= f.origin.x + f.size.width &&\n"
        "      mouse.y >= f.origin.y && mouse.y <= f.origin.y + f.size.height) {\n"
        "    chosen = s;\n"
        "    break;\n"
        "  }\n"
        "}\n"
        "var vf = chosen.visibleFrame;\n"
        "var tkL = Math.round(vf.origin.x);\n"
        "var tkT = Math.round(ph - vf.origin.y - vf.size.height);\n"
        "var tkR = Math.round(tkL + vf.size.width);\n"
        "var tkB = Math.round(tkT + vf.size.height);\n"
        "tkL + ',' + tkT + ',' + tkR + ',' + tkB;\n"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    parts = (proc.stdout or "").strip().split(",")
    if len(parts) != 4:
        return None
    try:
        left, top, right, bottom = (int(p.strip()) for p in parts)
    except ValueError:
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def bind_wraplength(
    label: tk.Widget,
    container: tk.Widget,
    *,
    inset: int = 0,
) -> None:
    """Keep *label* wraplength equal to container width minus *inset*.

    *inset* is a padding allowance (typically dialog frame padding × 2).
    """

    def _on_configure(event: tk.Event) -> None:
        width = int(getattr(event, "width", 0) or 0)
        if width <= 1:
            try:
                width = int(container.winfo_width())
            except tk.TclError:
                return
        if width <= 1:
            return
        wrap = max(1, width - inset)
        try:
            label.configure(wraplength=wrap)
        except tk.TclError:
            return

    container.bind("<Configure>", _on_configure, add="+")
    try:
        width = int(container.winfo_width())
    except tk.TclError:
        width = 0
    if width > 1:
        try:
            label.configure(wraplength=max(1, width - inset))
        except tk.TclError:
            pass


def make_dialog(
    parent: tk.Tk | tk.Toplevel,
    title: str,
    *,
    resizable: bool = True,
    padding: int = 16,
    grab: bool = False,
    transient: bool = True,
) -> tuple[tk.Toplevel, ttk.Frame]:
    """Create a standard Toplevel with a padded content frame."""
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    if transient:
        dlg.transient(parent)
    if grab:
        dlg.grab_set()
    dlg.resizable(resizable, resizable)

    frm = ttk.Frame(dlg, padding=padding)
    frm.grid(row=0, column=0, sticky="nsew")
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(0, weight=1)
    frm.columnconfigure(0, weight=1)
    return dlg, frm


def place_dialog_over_parent(dlg: tk.Toplevel, parent: tk.Misc) -> None:
    """Center *dlg* over *parent* after layout."""
    dlg.update_idletasks()
    dlg.geometry(
        center_over_window_geometry(
            parent.winfo_rootx(),
            parent.winfo_rooty(),
            max(parent.winfo_width(), 1),
            max(parent.winfo_height(), 1),
            max(dlg.winfo_reqwidth(), dlg.winfo_width(), 1),
            max(dlg.winfo_reqheight(), dlg.winfo_height(), 1),
        )
    )


def tree_with_yscroll(
    parent: tk.Widget,
    *,
    columns: tuple[str, ...] = (),
    show: str = "tree",
    selectmode: str = "extended",
    height: int = 12,
) -> tuple[ttk.Treeview, ttk.Scrollbar]:
    """Create a Treeview with a vertical scrollbar; caller grids them."""
    tree = ttk.Treeview(
        parent,
        columns=columns,
        show=show,
        selectmode=selectmode,
        height=height,
    )
    scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    return tree, scroll


def listbox_with_yscroll(
    parent: tk.Widget,
    *,
    height: int = 8,
    width: int = 72,
) -> tuple[tk.Listbox, ttk.Scrollbar]:
    """Create a Listbox with a vertical scrollbar; caller grids them."""
    listbox = tk.Listbox(parent, height=height, width=width)
    scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=listbox.yview)
    listbox.configure(yscrollcommand=scroll.set)
    return listbox, scroll


def path_row(
    parent: tk.Widget,
    *,
    row: int,
    label: str,
    textvariable: tk.StringVar,
    pad: dict | None = None,
    entry_state: str | None = None,
    entry_cursor: str | None = None,
) -> tuple[ttk.Label, ttk.Entry, ttk.Frame]:
    """Labeled entry spanning the middle column plus a trailing button frame."""
    pad = pad or {"padx": 10, "pady": 4}
    lbl = ttk.Label(parent, text=label)
    lbl.grid(row=row, column=0, sticky="w", **pad)
    entry_kw: dict = {"textvariable": textvariable}
    if entry_state is not None:
        entry_kw["state"] = entry_state
    if entry_cursor is not None:
        entry_kw["cursor"] = entry_cursor
    entry = ttk.Entry(parent, **entry_kw)
    entry.grid(row=row, column=1, sticky="ew", **pad)
    btns = ttk.Frame(parent)
    btns.grid(row=row, column=2, sticky="e", **pad)
    return lbl, entry, btns


class HoverTooltip:
    """Minimal Tk Enter/Leave balloon (no third-party tooltip library)."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event: object = None) -> None:
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            tip,
            text=self.text,
            relief=tk.SOLID,
            borderwidth=1,
            padding=(6, 3),
            wraplength=320,
        )
        label.pack()
        self._tip = tip

    def _hide(self, _event: object = None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class SearchPlaceholder:
    """Grey hint text for a StringVar; query ignores the hint itself."""

    def __init__(self, var: tk.StringVar, text: str) -> None:
        self.var = var
        self.text = text
        self.showing = False

    def show(self) -> None:
        self.showing = True
        self.var.set(self.text)

    def clear(self) -> None:
        if not self.showing:
            return
        self.showing = False
        self.var.set("")

    def query(self) -> str:
        raw = self.var.get()
        if self.showing and raw == self.text:
            return ""
        self.showing = False
        return raw.strip()

    def bind(self, entry: ttk.Entry) -> None:
        entry.bind("<FocusIn>", lambda _e: self.clear())
        entry.bind(
            "<FocusOut>",
            lambda _e: self.show() if not self.var.get().strip() else None,
        )
