#!/usr/bin/env python3
"""Capture Convert preview, finish report, and Import XML Edit for the v2 guide.

Drives the same GUI code as the packaged app (src/), with seeded prefs and the
guide-demo XML. Writes PNGs under docs/images/macos-app-v2/.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "docs" / "images" / "macos-app-v2"
SHOT_HOME = ROOT / ".screenshot-home"
BUNDLE = (
    SHOT_HOME
    / "Library"
    / "Application Support"
    / "io.github.slnnzmtl.rekordboxWavConverter"
)
XML = SHOT_HOME / "Documents" / "guide-demo.xml"
LIB = Path.home() / "Documents" / "rekordbox-converter"


def _window_id(title_substr: str) -> int | None:
    import ctypes
    import ctypes.util
    from ctypes import byref, c_bool, c_double, c_int, c_uint32, c_void_p

    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    cg.CGWindowListCopyWindowInfo.restype = c_void_p
    cg.CGWindowListCopyWindowInfo.argtypes = [c_uint32, c_uint32]
    cf.CFArrayGetCount.argtypes = [c_void_p]
    cf.CFArrayGetCount.restype = c_int
    cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_int]
    cf.CFArrayGetValueAtIndex.restype = c_void_p
    cf.CFDictionaryGetValue.argtypes = [c_void_p, c_void_p]
    cf.CFDictionaryGetValue.restype = c_void_p
    cf.CFStringCreateWithCString.restype = c_void_p
    cf.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]
    cf.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, c_int, c_uint32]
    cf.CFStringGetCString.restype = c_bool
    cf.CFNumberGetValue.argtypes = [c_void_p, c_int, c_void_p]
    cf.CFNumberGetValue.restype = c_bool
    cf.CFRelease.argtypes = [c_void_p]

    def cfstr(s: str):
        return cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)

    def pystr(ref) -> str:
        if not ref:
            return ""
        buf = ctypes.create_string_buffer(1024)
        ok = cf.CFStringGetCString(ref, buf, 1024, 0x08000100)
        return buf.value.decode() if ok else ""

    def pyn(ref) -> int | None:
        if not ref:
            return None
        v = c_int()
        return v.value if cf.CFNumberGetValue(ref, 3, byref(v)) else None

    def pyf(ref) -> float | None:
        if not ref:
            return None
        v = c_double()
        return v.value if cf.CFNumberGetValue(ref, 13, byref(v)) else None

    owner_k = cfstr("kCGWindowOwnerName")
    name_k = cfstr("kCGWindowName")
    num_k = cfstr("kCGWindowNumber")
    bounds_k = cfstr("kCGWindowBounds")
    xk, yk, wk, hk = cfstr("X"), cfstr("Y"), cfstr("Width"), cfstr("Height")
    arr = cg.CGWindowListCopyWindowInfo(0, 0)
    best = None
    best_area = 0.0
    for i in range(cf.CFArrayGetCount(arr)):
        d = cf.CFArrayGetValueAtIndex(arr, i)
        name = pystr(cf.CFDictionaryGetValue(d, name_k))
        if title_substr not in name:
            continue
        b = cf.CFDictionaryGetValue(d, bounds_k)
        w = pyf(cf.CFDictionaryGetValue(b, wk)) or 0
        h = pyf(cf.CFDictionaryGetValue(b, hk)) or 0
        area = w * h
        if area > best_area:
            best_area = area
            best = pyn(cf.CFDictionaryGetValue(d, num_k))
    cf.CFRelease(arr)
    return best


def _bounds_by_title(title_substr: str) -> tuple[float, float, float, float] | None:
    import ctypes
    import ctypes.util
    from ctypes import byref, c_bool, c_double, c_int, c_uint32, c_void_p

    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    cg.CGWindowListCopyWindowInfo.restype = c_void_p
    cg.CGWindowListCopyWindowInfo.argtypes = [c_uint32, c_uint32]
    cf.CFArrayGetCount.argtypes = [c_void_p]
    cf.CFArrayGetCount.restype = c_int
    cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_int]
    cf.CFArrayGetValueAtIndex.restype = c_void_p
    cf.CFDictionaryGetValue.argtypes = [c_void_p, c_void_p]
    cf.CFDictionaryGetValue.restype = c_void_p
    cf.CFStringCreateWithCString.restype = c_void_p
    cf.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]
    cf.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, c_int, c_uint32]
    cf.CFStringGetCString.restype = c_bool
    cf.CFNumberGetValue.argtypes = [c_void_p, c_int, c_void_p]
    cf.CFNumberGetValue.restype = c_bool
    cf.CFRelease.argtypes = [c_void_p]

    def cfstr(s: str):
        return cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)

    def pystr(ref) -> str:
        if not ref:
            return ""
        buf = ctypes.create_string_buffer(1024)
        ok = cf.CFStringGetCString(ref, buf, 1024, 0x08000100)
        return buf.value.decode() if ok else ""

    def pyf(ref) -> float | None:
        if not ref:
            return None
        v = c_double()
        return v.value if cf.CFNumberGetValue(ref, 13, byref(v)) else None

    name_k = cfstr("kCGWindowName")
    bounds_k = cfstr("kCGWindowBounds")
    xk, yk, wk, hk = cfstr("X"), cfstr("Y"), cfstr("Width"), cfstr("Height")
    arr = cg.CGWindowListCopyWindowInfo(0, 0)
    best = None
    best_area = 0.0
    for i in range(cf.CFArrayGetCount(arr)):
        d = cf.CFArrayGetValueAtIndex(arr, i)
        name = pystr(cf.CFDictionaryGetValue(d, name_k))
        if title_substr not in name:
            continue
        b = cf.CFDictionaryGetValue(d, bounds_k)
        x = pyf(cf.CFDictionaryGetValue(b, xk))
        y = pyf(cf.CFDictionaryGetValue(b, yk))
        w = pyf(cf.CFDictionaryGetValue(b, wk))
        h = pyf(cf.CFDictionaryGetValue(b, hk))
        if None in (x, y, w, h):
            continue
        area = w * h
        if area > best_area:
            best_area = area
            best = (x, y, w, h)
    cf.CFRelease(arr)
    return best


def capture(path: Path, *, title_substr: str | None = None, wid: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if wid is None and title_substr:
        wid = _window_id(title_substr)
    if wid is not None:
        subprocess.run(["screencapture", "-x", f"-l{wid}", str(path)], check=False)
        if path.is_file() and path.stat().st_size > 20_000:
            return
    if title_substr:
        b = _bounds_by_title(title_substr)
        if b:
            x, y, w, h = b
            # Tk dialogs often fail -l; use -R in global points
            region = f"{int(x)},{int(y)},{int(w)},{int(h)}"
            subprocess.run(["screencapture", "-x", f"-R{region}", str(path)], check=False)


def main() -> int:
    os.environ["HOME"] = str(SHOT_HOME)
    sys.path.insert(0, str(SRC))

    if not XML.is_file():
        print(f"missing {XML}", file=sys.stderr)
        return 1
    if not LIB.is_dir():
        print(f"missing library {LIB}", file=sys.stderr)
        return 1

    BUNDLE.mkdir(parents=True, exist_ok=True)
    (BUNDLE / "preferences.json").write_text(
        "{\n"
        '  "version": 1,\n'
        '  "analytics": "off",\n'
        f'  "source_xml": "{XML}",\n'
        f'  "library_dir": "{LIB}",\n'
        '  "output_format": "aiff",\n'
        '  "bit_depth": "16",\n'
        '  "sample_rate": "44100"\n'
        "}\n",
        encoding="utf-8",
    )

    import tkinter as tk

    from gui.app import ConverterApp
    from gui import dialogs as gui_dialogs
    from convert.models import FinishResultGroup, FinishResultRow
    from tkinter import ttk

    root = tk.Tk()
    root.title("Simple Rekordbox Converter 2.0.0")
    app = ConverterApp(root)
    root.geometry("1120x748+80+60")

    state: dict[str, object] = {"step": "wait_load"}

    def after_load() -> None:
        # Select all playlist tree leaves
        tree = app.playlist_tree
        for iid in tree.get_children(""):
            tree.selection_add(iid)
            for child in tree.get_children(iid):
                tree.selection_add(child)
        tree.event_generate("<<TreeviewSelect>>")
        root.update_idletasks()
        capture(OUT / "02-main-window.png", title_substr="Simple Rekordbox Converter 2.0.0")
        state["step"] = "preview"
        root.after(400, start_preview)

    def start_preview() -> None:
        app._start_convert()
        root.after(800, capture_preview)

    def capture_preview(tries: int = 0) -> None:
        names = [
            w.title()
            for w in root.winfo_children()
            if isinstance(w, tk.Toplevel) and w.winfo_exists()
        ]
        capture(OUT / "03-convert-preview.png", title_substr="Conversion preview")
        p = OUT / "03-convert-preview.png"
        if p.is_file() and p.stat().st_size > 20_000:
            state["step"] = "finish"
            root.after(300, show_finish)
            return
        if tries < 40:
            root.after(250, lambda: capture_preview(tries + 1))
            return
        print("preview capture failed; titles=", names, file=sys.stderr)
        state["step"] = "finish"
        root.after(100, show_finish)

    def show_finish() -> None:
        # Close any open toplevels (e.g. preview)
        for w in list(root.winfo_children()):
            if isinstance(w, tk.Toplevel):
                try:
                    w.destroy()
                except tk.TclError:
                    pass
        rows = [
            FinishResultRow(
                track="AURAFORCE - Prototype",
                status="Reused",
                detail="AIFF/AURAFORCE - Prototype.aiff",
            ),
            FinishResultRow(
                track="Acid DJ - Modulart",
                status="Reused",
                detail="AIFF/Acid DJ - Modulart.aiff",
            ),
            FinishResultRow(
                track="Demo - Missing Source",
                status="Missing skipped",
                detail="source not on disk",
            ),
        ]
        groups = [
            FinishResultGroup(
                header="Guide demo [AIFF] — Partial (2 reused, 1 missing skipped)",
                rows=rows,
            )
        ]
        from gui.layout import place_dialog_over_parent

        def place_and_schedule(dlg: tk.Toplevel) -> None:
            place_dialog_over_parent(dlg, root)

            def expand_trees(widget: tk.Misc = dlg) -> None:
                if isinstance(widget, ttk.Treeview):
                    for iid in widget.get_children(""):
                        widget.item(iid, open=True)
                for child in widget.winfo_children():
                    expand_trees(child)

            root.after(50, expand_trees)
            root.after(450, lambda: capture_finish_then_close(dlg))

        # Blocks on wait_window until dlg destroyed by capture_finish_then_close
        gui_dialogs.show_done_dialog(
            root,
            "Partial — 2 reused, 1 missing skipped.",
            output_folder=LIB,
            reveal=lambda p: None,
            on_open_guide=lambda: None,
            place_over=place_and_schedule,
            title="Partial",
            result_groups=groups,
            guidance=(
                "Import XML: "
                f"{LIB / 'rekordbox-import.xml'}\n"
                "In Rekordbox: Preferences → Advanced → Database → rekordbox xml "
                "→ Imported Library, then refresh and Import Playlist."
            ),
        )
        root.after(200, start_edit)

    def capture_finish_then_close(dlg: tk.Toplevel, tries: int = 0) -> None:
        capture(OUT / "04-finish-report.png", title_substr="Partial")
        p = OUT / "04-finish-report.png"
        if p.is_file() and p.stat().st_size > 20_000:
            try:
                dlg.destroy()
            except tk.TclError:
                pass
            return
        if tries < 15:
            root.after(200, lambda: capture_finish_then_close(dlg, tries + 1))
            return
        print("finish capture failed", file=sys.stderr)
        try:
            dlg.destroy()
        except tk.TclError:
            pass

    def start_edit() -> None:
        # Destroying the preview without Back left convert busy; clear it.
        try:
            app._set_busy(False)
        except Exception as exc:  # noqa: BLE001
            print("set_busy", exc, file=sys.stderr)
        try:
            app._enter_import_edit_mode()
        except Exception as exc:  # noqa: BLE001 — capture best-effort
            print("edit enter failed", exc, file=sys.stderr)
            root.after(200, quit_app)
            return
        root.after(800, capture_edit)

    def capture_edit(tries: int = 0) -> None:
        capture(
            OUT / "05-import-xml-edit.png",
            title_substr="Editing Import XML",
        )
        p = OUT / "05-import-xml-edit.png"
        if not (p.is_file() and p.stat().st_size > 20_000):
            capture(
                OUT / "05-import-xml-edit.png",
                title_substr="Simple Rekordbox Converter 2.0.0",
            )
        if tries < 10 and not (
            p.is_file() and p.stat().st_size > 20_000
        ):
            root.after(200, lambda: capture_edit(tries + 1))
            return
        root.after(400, quit_app)

    def quit_app() -> None:
        try:
            root.destroy()
        except tk.TclError:
            pass

    root.after(1500, after_load)
    root.mainloop()
    print("wrote:")
    for name in (
        "02-main-window.png",
        "03-convert-preview.png",
        "04-finish-report.png",
        "05-import-xml-edit.png",
    ):
        p = OUT / name
        print(f"  {p} exists={p.is_file()} size={p.stat().st_size if p.is_file() else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
