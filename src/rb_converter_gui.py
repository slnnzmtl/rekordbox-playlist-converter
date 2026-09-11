#!/usr/bin/env python3
"""Tiny tkinter front-end for the Rekordbox Playlist converter."""

from __future__ import annotations

import sys

from gui_preferences import (
    DOCUMENTS_PROBE_FLAG,
    FIND_REKORDBOX_XML_FLAG,
    IMPORT_XML_NAME,
    default_output_paths,
    find_rekordbox_xml_via_child,
    load_preferences,
    probe_path_via_child,
    resolve_startup_paths,
    run_documents_probe_cli,
    run_find_rekordbox_xml_cli,
    save_preferences,
)

# Same-app TCC child must not import tkinter (slow) or show a window.
if DOCUMENTS_PROBE_FLAG in sys.argv:
    raise SystemExit(run_documents_probe_cli(sys.argv[1:]))
if FIND_REKORDBOX_XML_FLAG in sys.argv:
    raise SystemExit(run_find_rekordbox_xml_cli(sys.argv[1:]))

import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import rb_playlist_to_wav as rb
from update_check import ReleaseInfo, UpdateCheckResult, check_for_update
from usage_guide import USAGE_GUIDE
from version import __version__

DEFAULT_WAV_DIR, DEFAULT_OUTPUT = default_output_paths(documents_accessible=True)
FALLBACK_WAV_DIR, FALLBACK_OUTPUT = default_output_paths(documents_accessible=False)
SEARCH_PLACEHOLDER = "Search playlists…"
APP_LOGO_NAME = "rpc-logo-white.png"
APP_WINDOW_ICON_NAME = "rpc-logo-white-256.png"
BIT_DEPTH_24_TOOLTIP = (
    "This is a maximum, not a target. "
    "16-bit tracks are not upconverted to 24-bit."
)
SAMPLE_RATE_48_TOOLTIP = (
    "This is a maximum, not a target. "
    "44.1 kHz tracks are not upconverted to 48 kHz."
)
BIT_DEPTH_LABELS = {"16": "16Bit", "24": "24Bit"}
SAMPLE_RATE_LABELS = {"44100": "44.1KHz", "48000": "48KHz"}
BIT_DEPTH_FROM_LABEL = {label: value for value, label in BIT_DEPTH_LABELS.items()}
SAMPLE_RATE_FROM_LABEL = {label: value for value, label in SAMPLE_RATE_LABELS.items()}
ACTION_BUTTON_WIDTH = 9


class _HoverTooltip:
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


def _bundled_asset(name: str) -> Path:
    """Resolve a file under assets/ (bundled when frozen)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "assets" / name
            if bundled.is_file():
                return bundled
        beside = Path(sys.executable).resolve().parent / "assets" / name
        if beside.is_file():
            return beside
    return Path(__file__).resolve().parent.parent / "assets" / name


def app_logo_path() -> Path:
    """Return the full-resolution app logo PNG."""
    return _bundled_asset(APP_LOGO_NAME)


def app_window_icon_path() -> Path:
    """Return the 256px window-icon PNG used by Tk."""
    return _bundled_asset(APP_WINDOW_ICON_NAME)


def total_successful_conversions(stats_list: list[rb.ConvertStats]) -> int:
    return sum(s.converted + s.copied for s in stats_list)


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


def _active_display_bounds() -> tuple[int, int, int, int] | None:
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


def open_in_finder(path: Path) -> None:
    """Reveal a folder in Finder (macOS) or the platform file browser."""
    target = path if path.is_dir() else path.parent
    if not target.exists():
        return
    subprocess.run(["open", str(target)], check=False)


class ConverterApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        documents_accessible: bool | None = None,
    ) -> None:
        self.root = root
        root.title(f"Rekordbox Playlist Converter {__version__}")
        root.minsize(560, 480)
        bounds = _active_display_bounds()
        if bounds is not None:
            root.geometry(fit_window_geometry(1120, 720, *bounds))
        else:
            root.geometry("1120x720")
        self.logo_image = self._apply_window_icon()

        self.xml_var = tk.StringVar()
        self.wav_dir_var = tk.StringVar()
        self.output_var = tk.StringVar()
        # Start with home fallback so the window can appear before Documents TCC.
        saved_prefs = load_preferences()
        startup_wav, startup_output = resolve_startup_paths(
            saved_prefs,
            default_wav_dir=FALLBACK_WAV_DIR,
            default_import_xml=FALLBACK_OUTPUT,
            documents_accessible=False,
        )
        self.wav_dir_var.set(str(startup_wav))
        self.output_var.set(str(startup_output))
        saved_format = saved_prefs.get("output_format", "wav")
        if saved_format not in ("wav", "aiff"):
            saved_format = "wav"
        self.format_var = tk.StringVar(value=saved_format)
        saved_depth = saved_prefs.get("bit_depth", "24")
        if saved_depth not in ("16", "24"):
            saved_depth = "24"
        self.bit_depth_var = tk.StringVar(value=saved_depth)
        saved_rate = saved_prefs.get("sample_rate", "48000")
        if saved_rate not in ("44100", "48000"):
            saved_rate = "48000"
        self.sample_rate_var = tk.StringVar(value=saved_rate)
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a Rekordbox XML export.")
        self.tracklist_total_var = tk.StringVar(value="")
        self._busy = False
        self._search_showing_placeholder = False
        self._usage_window: tk.Toplevel | None = None
        self._update_modal_shown = False
        self._progress_target = 0.0
        self._progress_anim_id: str | None = None
        self._documents_accessible = False
        self._source_root = None
        # (kind, folder, name, track_count, node) for every node in the XML walk
        self._playlist_entries: list[tuple[str, str, str, int, object]] = []
        # iid -> (kind, folder, name) for rows currently in the tree
        self._playlist_iids: dict[str, tuple[str, str, str]] = {}

        self._build()
        self.search_var.trace_add("write", lambda *_: self._apply_playlist_filter())
        if not self.search_var.get():
            self._show_search_placeholder()
        # Prefs key, not xml_var: skip first-launch search even if Documents
        # restore has not filled the field yet.
        self._has_saved_source_xml = bool(saved_prefs.get("source_xml", "").strip())
        self._restore_saved_source_xml(saved_prefs)
        if documents_accessible is None:
            # Never list Documents in this process during init: macOS TCC
            # blocks every thread until the user answers, so the window would
            # never appear if they dismiss the dialog.
            self.root.after_idle(self._start_documents_probe)
        else:
            self._apply_documents_access(documents_accessible)
        self._start_update_check(manual=False)

    def _restore_saved_source_xml(self, saved: dict[str, str]) -> None:
        raw = saved.get("source_xml", "").strip()
        if not raw:
            return
        candidate = Path(raw).expanduser()
        if not self._documents_accessible and rb.path_is_under_documents(candidate):
            return
        try:
            if not candidate.is_file():
                return
        except OSError:
            return
        self._adopt_source_xml(candidate)

    def _adopt_source_xml(self, path: Path) -> None:
        self.xml_var.set(str(path))
        self._load_playlists()

    @property
    def documents_accessible(self) -> bool:
        return self._documents_accessible

    def _start_documents_probe(self) -> None:
        def worker() -> None:
            if not self._has_saved_source_xml:
                # Search known filenames first so a dismissed Documents listing
                # prompt cannot block XML autoload on first launch.
                hits = find_rekordbox_xml_via_child(Path.home())
                self._ui(lambda paths=hits: self._apply_xml_search_hits(paths))
            accessible = probe_path_via_child(Path.home() / "Documents")
            self._ui(lambda a=accessible: self._apply_documents_access(a))

        threading.Thread(target=worker, daemon=True).start()

    def _probe_documents_after_idle(self) -> None:
        if not self._has_saved_source_xml:
            hits = find_rekordbox_xml_via_child(Path.home())
            self._apply_xml_search_hits(hits)
        accessible = probe_path_via_child(Path.home() / "Documents")
        self._apply_documents_access(accessible)

    def _apply_xml_search_hits(self, paths: list[Path]) -> None:
        if self.xml_var.get().strip():
            return
        if len(paths) == 1:
            self._adopt_source_xml(paths[0])
        elif len(paths) >= 2:
            self._show_xml_choice_modal(paths)

    def _show_xml_choice_modal(self, paths: list[Path]) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose Rekordbox XML")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(True, True)

        frm = ttk.Frame(dlg, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(1, weight=1)

        ttk.Label(
            frm,
            text="Several Rekordbox XML files were found. Choose one to load:",
            wraplength=480,
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        list_frame = ttk.Frame(frm)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        listbox = tk.Listbox(list_frame, height=min(8, max(3, len(paths))), width=72)
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        listbox.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for path in paths:
            listbox.insert(tk.END, str(path))
        listbox.selection_set(0)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, sticky="e", pady=(12, 0))

        def close() -> None:
            dlg.destroy()

        def open_selected() -> None:
            selection = listbox.curselection()
            if not selection:
                return
            chosen = paths[int(selection[0])]
            self._adopt_source_xml(chosen)
            self._persist_output_preferences(include_source_xml=True)
            close()

        ttk.Button(btns, text="Cancel", command=close).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="Open", command=open_selected).pack(side=tk.LEFT)
        listbox.bind("<Double-Button-1>", lambda _e: open_selected())
        dlg.bind("<Return>", lambda _e: open_selected())
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.wait_window()

    def _apply_documents_access(self, override: bool | None) -> None:
        if override is None:
            accessible = False
        else:
            accessible = override
        self._documents_accessible = accessible
        if accessible:
            saved = load_preferences()
            docs_wav, docs_xml = default_output_paths(documents_accessible=True)
            startup_wav, startup_output = resolve_startup_paths(
                saved,
                default_wav_dir=docs_wav,
                default_import_xml=docs_xml,
                documents_accessible=True,
            )
            self.wav_dir_var.set(str(startup_wav))
            self.output_var.set(str(startup_output))
            if not self.xml_var.get().strip():
                self._restore_saved_source_xml(saved)

    def _apply_window_icon(self) -> tk.PhotoImage | None:
        path = app_window_icon_path()
        if not path.is_file():
            return None
        try:
            image = tk.PhotoImage(file=str(path))
            self.root.iconphoto(True, image)
            return image
        except tk.TclError:
            return None

    def _build(self) -> None:
        self._build_menubar()

        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self.root, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(2, weight=1)

        ttk.Label(frm, text="Rekordbox XML").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.xml_var).grid(
            row=0, column=1, sticky="ew", **pad
        )
        xml_btns = ttk.Frame(frm)
        xml_btns.grid(row=0, column=2, sticky="e", **pad)
        ttk.Button(
            xml_btns,
            text="Browse…",
            width=ACTION_BUTTON_WIDTH,
            command=self._browse_xml,
        ).pack(side=tk.LEFT)
        self.refresh_btn = ttk.Button(
            xml_btns, text="Refresh", command=self._refresh_xml
        )
        self.refresh_btn.pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(frm, text="Playlists").grid(row=1, column=0, sticky="w", **pad)
        self.search_entry = ttk.Entry(frm, textvariable=self.search_var)
        self.search_entry.grid(row=1, column=1, sticky="ew", **pad)
        self.search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry.bind("<FocusOut>", self._on_search_focus_out)
        ttk.Label(frm, text="Hold ⌃ to multi-select").grid(
            row=1, column=2, sticky="e", **pad
        )

        list_frame = ttk.Frame(frm)
        list_frame.grid(row=2, column=0, columnspan=3, sticky="nsew", **pad)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        panes = ttk.Panedwindow(list_frame, orient=tk.HORIZONTAL)
        panes.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.playlist_tree = ttk.Treeview(
            left,
            show="tree",
            selectmode="extended",
            height=12,
        )
        scroll = ttk.Scrollbar(
            left, orient=tk.VERTICAL, command=self.playlist_tree.yview
        )
        self.playlist_tree.configure(yscrollcommand=scroll.set)
        self.playlist_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.playlist_tree.bind("<<TreeviewSelect>>", self._on_playlist_select, add="+")

        right = ttk.Frame(panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.tracklist_tree = ttk.Treeview(
            right,
            show="tree",
            selectmode="none",
            height=12,
        )
        track_scroll = ttk.Scrollbar(
            right, orient=tk.VERTICAL, command=self.tracklist_tree.yview
        )
        self.tracklist_tree.configure(yscrollcommand=track_scroll.set)
        self.tracklist_tree.grid(row=0, column=0, sticky="nsew")
        track_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Label(right, textvariable=self.tracklist_total_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        panes.add(left, weight=1)
        panes.add(right, weight=1)
        self._refresh_tracklist_preview()

        ttk.Label(frm, text="Output folder").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.wav_dir_var).grid(
            row=3, column=1, sticky="ew", **pad
        )
        ttk.Button(
            frm,
            text="Browse…",
            width=ACTION_BUTTON_WIDTH,
            command=self._browse_wav_dir,
        ).grid(row=3, column=2, sticky="e", **pad)

        ttk.Label(frm, text="Import XML").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.output_var).grid(
            row=4, column=1, sticky="ew", **pad
        )
        ttk.Button(
            frm,
            text="Browse…",
            width=ACTION_BUTTON_WIDTH,
            command=self._browse_output,
        ).grid(row=4, column=2, sticky="e", **pad)

        ttk.Label(frm, text="Format").grid(row=5, column=0, sticky="w", **pad)
        format_opts = ttk.Frame(frm)
        format_opts.grid(row=5, column=1, sticky="w", **pad)
        ttk.Radiobutton(
            format_opts, text="WAV", variable=self.format_var, value="wav"
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            format_opts, text="AIFF", variable=self.format_var, value="aiff"
        ).pack(side=tk.LEFT, padx=(8, 0))
        self.convert_btn = ttk.Button(
            frm,
            text="Convert",
            width=ACTION_BUTTON_WIDTH,
            command=self._start_convert,
        )
        self.convert_btn.grid(row=5, column=2, sticky="e", **pad)

        quality = ttk.Frame(frm)
        quality.grid(row=6, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(quality, text="Sampling format").pack(side=tk.LEFT)
        self.bit_depth_combo = ttk.Combobox(
            quality,
            values=list(BIT_DEPTH_LABELS.values()),
            state="readonly",
            width=8,
        )
        self.bit_depth_combo.set(
            BIT_DEPTH_LABELS.get(self.bit_depth_var.get(), "24Bit")
        )
        self.bit_depth_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.bit_depth_combo.bind(
            "<<ComboboxSelected>>", self._on_bit_depth_selected, add="+"
        )
        _HoverTooltip(self.bit_depth_combo, BIT_DEPTH_24_TOOLTIP)
        self.sample_rate_combo = ttk.Combobox(
            quality,
            values=list(SAMPLE_RATE_LABELS.values()),
            state="readonly",
            width=9,
        )
        self.sample_rate_combo.set(
            SAMPLE_RATE_LABELS.get(self.sample_rate_var.get(), "48KHz")
        )
        self.sample_rate_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.sample_rate_combo.bind(
            "<<ComboboxSelected>>", self._on_sample_rate_selected, add="+"
        )
        _HoverTooltip(self.sample_rate_combo, SAMPLE_RATE_48_TOOLTIP)

        self.progress = ttk.Progressbar(frm, mode="determinate", maximum=100)
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", **pad)
        self.progress["value"] = 0

        ttk.Label(frm, textvariable=self.status_var, wraplength=1000).grid(
            row=8, column=0, columnspan=3, sticky="ew", **pad
        )

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="Search for Rekordbox XML…",
            command=self._search_rekordbox_xml,
        )
        menubar.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(
            label="How to Use…",
            command=self._show_usage_guide,
            accelerator="Command-?",
        )
        help_menu.add_command(
            label="Check for Updates…",
            command=self._check_for_updates_manual,
        )
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)
        try:
            self.root.bind_all("<Command-?>", lambda _e: self._show_usage_guide())
            self.root.bind_all("<Command-Shift-/>", lambda _e: self._show_usage_guide())
        except tk.TclError:
            pass

    def _search_rekordbox_xml(self) -> None:
        if self._busy:
            return
        hits = find_rekordbox_xml_via_child(Path.home())
        if len(hits) == 1:
            self._adopt_source_xml(hits[0])
            self._persist_output_preferences(include_source_xml=True)
        elif len(hits) >= 2:
            self._show_xml_choice_modal(hits)

    def _show_usage_guide(self) -> None:
        if self._usage_window is not None and self._usage_window.winfo_exists():
            self._usage_window.lift()
            self._usage_window.focus_force()
            return

        dlg = tk.Toplevel(self.root)
        self._usage_window = dlg
        dlg.title("How to use")
        dlg.transient(self.root)
        dlg.geometry("640x520")
        dlg.minsize(480, 360)

        frm = ttk.Frame(dlg, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)

        text = scrolledtext.ScrolledText(
            frm, wrap=tk.WORD, width=72, height=28, font=("Menlo", 11)
        )
        text.grid(row=0, column=0, sticky="nsew")
        text.insert("1.0", USAGE_GUIDE.strip() + "\n")
        text.configure(state=tk.DISABLED)

        def close() -> None:
            self._usage_window = None
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Close", command=close).pack(side=tk.RIGHT)
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = (
            self.root.winfo_rooty()
            + (self.root.winfo_height() - dlg.winfo_height()) // 2
        )
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.focus_force()

    def _on_bit_depth_selected(self, _event: object = None) -> None:
        label = self.bit_depth_combo.get().strip()
        self.bit_depth_var.set(BIT_DEPTH_FROM_LABEL.get(label, "24"))

    def _on_sample_rate_selected(self, _event: object = None) -> None:
        label = self.sample_rate_combo.get().strip()
        self.sample_rate_var.set(SAMPLE_RATE_FROM_LABEL.get(label, "48000"))

    def _resolved_output_paths(self) -> tuple[Path, Path]:
        wav_dir = Path(self.wav_dir_var.get().strip() or str(DEFAULT_WAV_DIR)).expanduser()
        output = Path(self.output_var.get().strip() or str(DEFAULT_OUTPUT)).expanduser()
        if not wav_dir.is_absolute():
            wav_dir = Path.home() / wav_dir
        if not output.is_absolute():
            output = Path.home() / output
        return wav_dir, output

    def _persist_output_preferences(self, *, include_source_xml: bool = False) -> None:
        wav_dir, output = self._resolved_output_paths()
        source_xml = None
        if include_source_xml:
            source_s = self.xml_var.get().strip()
            source_xml = Path(source_s).expanduser() if source_s else None
        fmt = self.format_var.get().strip().lower()
        if fmt not in ("wav", "aiff"):
            fmt = "wav"
        depth = self.bit_depth_var.get().strip()
        if depth not in ("16", "24"):
            depth = "24"
        rate = self.sample_rate_var.get().strip()
        if rate not in ("44100", "48000"):
            rate = "48000"
        try:
            save_preferences(
                wav_dir,
                output,
                source_xml=source_xml,
                output_format=fmt,
                bit_depth=depth,
                sample_rate=rate,
            )
        except OSError:
            pass

    def _browse_initial_dir(self, preferred: Path | None = None) -> str:
        """Pick a file-dialog start folder without stating Documents when denied."""
        if preferred is not None:
            preferred_s = str(preferred).strip()
            if preferred_s:
                preferred_path = Path(preferred_s).expanduser()
                if self._documents_accessible or not rb.path_is_under_documents(
                    preferred_path
                ):
                    return str(preferred_path)
        if self._documents_accessible:
            return str(Path.home() / "Documents")
        return str(Path.home())

    def _browse_xml(self) -> None:
        current = self.xml_var.get().strip()
        preferred = Path(current).expanduser().parent if current else None
        path = filedialog.askopenfilename(
            title="Rekordbox XML export",
            initialdir=self._browse_initial_dir(preferred),
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if path:
            self._adopt_source_xml(Path(path))
            self._persist_output_preferences(include_source_xml=True)

    def _refresh_xml(self) -> None:
        if self._busy:
            return
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            messagebox.showerror("Missing XML", "Choose a Rekordbox XML export.")
            return
        self._load_playlists()

    def _browse_wav_dir(self) -> None:
        current = self.wav_dir_var.get().strip()
        path = filedialog.askdirectory(
            title="Audio output folder",
            initialdir=self._browse_initial_dir(
                Path(current) if current else FALLBACK_WAV_DIR
            ),
        )
        if path:
            self.wav_dir_var.set(path)
            self._persist_output_preferences()

    def _browse_output(self) -> None:
        current = self.output_var.get().strip()
        if current:
            preferred = Path(current).expanduser().parent
        else:
            preferred = FALLBACK_WAV_DIR
        path = filedialog.asksaveasfilename(
            title="Import XML",
            initialfile=Path(current).name if current else IMPORT_XML_NAME,
            initialdir=self._browse_initial_dir(preferred),
            defaultextension=".xml",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)
            self._persist_output_preferences()

    def _load_playlists(self) -> None:
        self.playlist_tree.delete(*self.playlist_tree.get_children())
        self._playlist_entries = []
        self._playlist_iids = {}
        self._source_root = None
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            self._refresh_tracklist_preview()
            return
        path = Path(xml_s).expanduser()
        if not path.is_file():
            self.status_var.set(f"XML not found: {path}")
            self._refresh_tracklist_preview()
            return
        try:
            root = rb.load_dj_playlists(path)
        except rb.CliError as exc:
            self.status_var.set(str(exc))
            self._refresh_tracklist_preview()
            return
        self._source_root = root
        nodes = rb.iter_playlist_nodes(root)
        for kind, folder, name, node in nodes:
            count = rb.playlist_track_count(node) if kind == "playlist" else 0
            self._playlist_entries.append((kind, folder, name, count, node))
        self._show_search_placeholder()
        self._apply_playlist_filter()
        playlist_count = sum(1 for kind, *_rest in self._playlist_entries if kind == "playlist")
        if playlist_count:
            self.status_var.set(
                f"Loaded {playlist_count} playlist(s). Select and Convert."
            )
        else:
            self.status_var.set("No playlists found in XML.")

    def _show_search_placeholder(self) -> None:
        self._search_showing_placeholder = True
        self.search_var.set(SEARCH_PLACEHOLDER)

    def _clear_search_placeholder(self) -> None:
        if not self._search_showing_placeholder:
            return
        self._search_showing_placeholder = False
        self.search_var.set("")

    def _on_search_focus_in(self, _event: object | None = None) -> None:
        self._clear_search_placeholder()

    def _on_search_focus_out(self, _event: object | None = None) -> None:
        if not self.search_var.get().strip():
            self._show_search_placeholder()

    def _search_query(self) -> str:
        if self._search_showing_placeholder:
            return ""
        return self.search_var.get().strip()

    @staticmethod
    def _playlist_iid(kind: str, folder: str, name: str) -> str:
        return f"{kind}:{rb.playlist_label(folder, name)}"

    @staticmethod
    def _playlist_row_text(kind: str, name: str, count: int) -> str:
        if kind == "folder":
            return name
        return f"{name} ({count} tracks)"

    def _apply_playlist_filter(self) -> None:
        query = self._search_query().casefold()
        self.playlist_tree.delete(*self.playlist_tree.get_children())
        self._playlist_iids = {}

        if query:
            keep: set[tuple[str, str, str]] = set()
            for kind, folder, name, count, _node in self._playlist_entries:
                if kind != "playlist":
                    continue
                label = rb.playlist_label(folder, name)
                display = f"{label} ({count} tracks)"
                haystack = f"{name} {label} {display}".casefold()
                if query not in haystack:
                    continue
                keep.add((kind, folder, name))
                # Ancestor folders for the matched playlist path.
                parts = [p for p in folder.split(" / ") if p] if folder else []
                for i in range(len(parts)):
                    anc_folder = " / ".join(parts[:i]) if i else ""
                    keep.add(("folder", anc_folder, parts[i]))
            visible = [
                entry
                for entry in self._playlist_entries
                if (entry[0], entry[1], entry[2]) in keep
            ]
        else:
            visible = list(self._playlist_entries)

        # Parent folder iid for a row: folder path maps to the folder node's iid.
        folder_iid_by_path: dict[str, str] = {"": ""}
        for kind, folder, name, count, _node in visible:
            iid = self._playlist_iid(kind, folder, name)
            parent_path = folder
            parent_iid = folder_iid_by_path.get(parent_path, "")
            text = self._playlist_row_text(kind, name, count)
            self.playlist_tree.insert(parent_iid, tk.END, iid=iid, text=text, open=False)
            self._playlist_iids[iid] = (kind, folder, name)
            if kind == "folder":
                child_path = rb.playlist_label(folder, name)
                folder_iid_by_path[child_path] = iid

        if query:
            for iid, (kind, _folder, _name) in self._playlist_iids.items():
                if kind == "folder":
                    self.playlist_tree.item(iid, open=True)

        self._refresh_tracklist_preview()

    def _on_playlist_select(self, _event: object = None) -> None:
        self._refresh_tracklist_preview()

    def _playlist_node(self, folder: str, name: str):
        for kind, entry_folder, entry_name, _count, node in self._playlist_entries:
            if kind == "playlist" and entry_folder == folder and entry_name == name:
                return node
        return None

    @staticmethod
    def _track_preview_label(track) -> str:
        if track is None:
            return "(missing track)"
        artist = track.get("Artist") or ""
        title = track.get("Name") or ""
        label = f"{artist} - {title}" if artist else title
        loc = track.get("Location") or ""
        path = rb.decode_location(loc) if loc else None
        if path is not None and path.suffix:
            return f"{label}{path.suffix.lower()}"
        return label

    def _refresh_tracklist_preview(self) -> None:
        self.tracklist_tree.delete(*self.tracklist_tree.get_children())
        self.tracklist_total_var.set("No tracks selected")
        if self._source_root is None:
            return
        selected = self._selected_playlists(unique_names=False)
        if not selected:
            return
        by_id, by_location = rb.collection_indexes(self._source_root)
        unique_keys: set[str] = set()
        painted = 0
        for folder, name in selected:
            node = self._playlist_node(folder, name)
            if node is None:
                continue
            count = rb.playlist_track_count(node)
            group_text = f"{name} ({count} tracks)"
            group_iid = self.tracklist_tree.insert(
                "", tk.END, text=group_text, open=True
            )
            painted += 1
            key_type = node.get("KeyType", "0")
            for entry in node.findall("TRACK"):
                key = entry.get("Key")
                track = None
                if key:
                    unique_keys.add(key)
                    if key_type == "1":
                        track = by_location.get(key)
                    else:
                        track = by_id.get(key)
                self.tracklist_tree.insert(
                    group_iid, tk.END, text=self._track_preview_label(track)
                )
        if unique_keys and painted:
            playlist_word = "playlist" if painted == 1 else "playlists"
            self.tracklist_total_var.set(
                f"{len(unique_keys)} unique tracks from {painted} {playlist_word}"
            )

    def _selected_playlists(self, *, unique_names: bool = True) -> list[tuple[str, str]]:
        chosen: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add_playlist(folder: str, name: str) -> None:
            entry = (folder, name)
            if entry not in seen:
                seen.add(entry)
                chosen.append(entry)

        def collect_from_iid(iid: str) -> None:
            meta = self._playlist_iids.get(iid)
            if meta is None:
                return
            kind, folder, name = meta
            if kind == "playlist":
                add_playlist(folder, name)
                return
            for child in self.playlist_tree.get_children(iid):
                collect_from_iid(child)

        for iid in self.playlist_tree.selection():
            collect_from_iid(iid)

        if unique_names:
            names = [name for _folder, name in chosen]
            # Same output playlist name `{name} [WAV]` — refuse converting two at once.
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                listed = ", ".join(sorted(dupes))
                raise rb.CliError(
                    f"cannot select multiple playlists with the same name: {listed}"
                )
        return chosen

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.convert_btn.configure(state=state)
        self.refresh_btn.configure(state=state)
        if busy:
            self._cancel_progress_anim()
            self._progress_target = 0.0
            self.progress["value"] = 0
        # On success finish_ok leaves the bar at 100; on error finish_error resets.

    def _cancel_progress_anim(self) -> None:
        if self._progress_anim_id is not None:
            self.root.after_cancel(self._progress_anim_id)
            self._progress_anim_id = None

    def _animate_progress_to(self, pct: float, *, snap: bool = False) -> None:
        self._progress_target = max(0.0, min(100.0, float(pct)))
        if snap:
            self._cancel_progress_anim()
            self.progress["value"] = self._progress_target
            return
        if self._progress_anim_id is None:
            self._tick_progress_anim()

    def _tick_progress_anim(self) -> None:
        self._progress_anim_id = None
        cur = float(self.progress["value"])
        target = self._progress_target
        diff = target - cur
        if abs(diff) < 0.2:
            self.progress["value"] = target
            return
        # Ease toward target (~60fps); never overshoot.
        step = diff * 0.22
        if abs(step) < 0.35:
            step = 0.35 if diff > 0 else -0.35
        nxt = cur + step
        if (diff > 0 and nxt > target) or (diff < 0 and nxt < target):
            nxt = target
        self.progress["value"] = nxt
        self._progress_anim_id = self.root.after(16, self._tick_progress_anim)

    def _set_progress(
        self, current: int, total: int, *, action: str = "", name: str = ""
    ) -> None:
        if total <= 0:
            pct = 100.0 if current else 0.0
        else:
            pct = min(100.0, 100.0 * current / total)
        self._animate_progress_to(pct)
        if action and name:
            self.status_var.set(f"{action.capitalize()} {name} ({current}/{total})…")
        elif total > 0:
            self.status_var.set(f"Working… {current}/{total} ({int(pct)}%)")

    def _ui(self, fn) -> None:
        self.root.after(0, fn)

    def _start_convert(self) -> None:
        if self._busy:
            return
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            messagebox.showerror("Missing XML", "Choose a Rekordbox XML export.")
            return
        try:
            selected = self._selected_playlists()
        except rb.CliError as exc:
            messagebox.showerror("Selection", str(exc))
            return
        if not selected:
            messagebox.showerror("Selection", "Select at least one playlist.")
            return
        wav_dir, output = self._resolved_output_paths()
        self._persist_output_preferences()
        output_format = self.format_var.get().strip().lower()
        if output_format not in ("wav", "aiff"):
            output_format = "wav"
        try:
            max_bit_depth = int(self.bit_depth_var.get().strip() or "24")
        except ValueError:
            max_bit_depth = 24
        if max_bit_depth not in (16, 24):
            max_bit_depth = 24
        try:
            max_sample_rate = int(self.sample_rate_var.get().strip() or "48000")
        except ValueError:
            max_sample_rate = 48000
        if max_sample_rate not in (44100, 48000):
            max_sample_rate = 48000
        xml_path = Path(xml_s).expanduser()

        self._set_busy(True)
        self.status_var.set("Preparing…")

        def worker() -> None:
            try:
                summaries: list[str] = []
                skipped: list[str] = []
                all_stats: list[rb.ConvertStats] = []
                playlist_dirs: list[Path] = []
                plans: list[rb.Plan] = []
                for i, (folder, name) in enumerate(selected):
                    label = f"{name} ({i + 1}/{len(selected)})"
                    self._ui(lambda l=label: self.status_var.set(f"Preparing {l}…"))
                    plan, errors = rb.prepare(
                        xml_path,
                        name,
                        wav_dir,
                        output,
                        playlist_folder=folder,
                        output_format=output_format,
                        max_bit_depth=max_bit_depth,
                        max_sample_rate=max_sample_rate,
                    )
                    if errors:
                        msg = "\n".join(errors)
                        self._ui(lambda m=msg: self._finish_error(m))
                        return
                    assert plan is not None
                    plans.append(plan)
                    skipped.extend(plan.warnings)
                    playlist_dirs.append(plan.playlist_dir)

                rb.share_output_root(plans)
                total = sum(len(plan.unique) for plan in plans)
                done_base = 0

                def on_progress(
                    current: int,
                    _plan_total: int,
                    action: str,
                    track_name: str,
                    base: int = 0,
                ) -> None:
                    overall = base + current
                    self._ui(
                        lambda o=overall, t=total, a=action, n=track_name: self._set_progress(
                            o, t, action=a, name=n
                        )
                    )

                for plan in plans:
                    base = done_base

                    def tick(
                        current: int,
                        plan_total: int,
                        action: str,
                        track_name: str,
                        b: int = base,
                    ) -> None:
                        on_progress(current, plan_total, action, track_name, base=b)

                    stats = rb.convert_unique(
                        plan, force=False, progress=False, on_progress=tick
                    )
                    all_stats.append(stats)
                    done_base += len(plan.unique)
                    stats.appended = rb.apply_xml(plan)
                    rb.atomic_write_xml(plan.output_root, plan.output)
                    parts = []
                    if stats.converted:
                        parts.append(f"{stats.converted} converted")
                    if stats.copied:
                        parts.append(f"{stats.copied} copied")
                    if stats.skipped:
                        parts.append(f"{stats.skipped} skipped")
                    if stats.appended:
                        parts.append(f"+{stats.appended} playlist entries")
                    if plan.warnings:
                        parts.append(f"{len(plan.warnings)} missing skipped")
                    detail = ", ".join(parts) if parts else "done"
                    summaries.append(f"{plan.wav_playlist_name}: {detail}")

                if total == 0:
                    self._ui(lambda: self._set_progress(0, 0))
                else:
                    self._ui(lambda t=total: self._set_progress(t, t))
                open_dir = playlist_dirs[0] if len(playlist_dirs) == 1 else wav_dir
                out = str(output)
                if total_successful_conversions(all_stats) == 0:
                    self._ui(
                        lambda s=summaries, w=skipped: self._finish_no_conversions(s, w)
                    )
                else:
                    self._ui(
                        lambda s=summaries, o=out, w=skipped, d=open_dir: self._finish_ok(
                            s, o, w, d
                        )
                    )
            except rb.CliError as exc:
                self._ui(lambda e=str(exc): self._finish_error(e))
            except Exception as exc:  # noqa: BLE001 — show unexpected errors in UI
                self._ui(lambda e=str(exc): self._finish_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_error(self, message: str) -> None:
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self.status_var.set("Failed.")
        messagebox.showerror("Conversion failed", message)

    def _finish_no_conversions(
        self,
        summaries: list[str],
        warnings: list[str] | None = None,
    ) -> None:
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self.status_var.set("Finished with no audio files converted or copied.")
        if warnings:
            self._show_missing_files_dialog(
                "No conversions",
                "These files were missing and were skipped:",
                warnings,
                summary="\n".join(summaries),
            )
            return
        messagebox.showwarning("No conversions", "\n".join(summaries))

    def _finish_ok(
        self,
        summaries: list[str],
        output: str,
        warnings: list[str] | None = None,
        open_dir: Path | None = None,
    ) -> None:
        self._set_busy(False)
        self._animate_progress_to(100, snap=True)
        body = "\n".join(summaries)
        self.status_var.set(
            f"Done. Point Rekordbox Imported Library at:\n{output}"
        )
        if warnings:
            self._show_missing_files_dialog(
                "Skipped missing tracks",
                "These files were missing and were skipped:",
                warnings,
            )
        fmt = self.format_var.get().strip().lower()
        suffix = "[AIFF]" if fmt == "aiff" else "[WAV]"
        message = (
            f"{body}\n\n"
            "Import into Rekordbox:\n"
            "1. Preferences → View → Layout → enable rekordbox xml\n"
            "2. Preferences → Advanced → Database → Imported Library →\n"
            f"   {output}\n"
            "3. Browser → rekordbox xml → Playlists → Import Playlist\n"
            f"   (or drag the {suffix} playlist into Playlists)"
        )
        self._show_done_dialog(message, open_dir)

    def _show_missing_files_dialog(
        self,
        title: str,
        intro: str,
        warnings: list[str],
        *,
        summary: str | None = None,
    ) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(True, True)

        frm = ttk.Frame(dlg, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(2 if summary else 1, weight=1)

        row = 0
        if summary:
            ttk.Label(frm, text=summary, justify=tk.LEFT, wraplength=520).grid(
                row=row, column=0, sticky="w", pady=(0, 8)
            )
            row += 1

        ttk.Label(frm, text=intro, wraplength=520).grid(
            row=row, column=0, sticky="w", pady=(0, 8)
        )
        row += 1

        list_frame = ttk.Frame(frm)
        list_frame.grid(row=row, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        listbox = tk.Listbox(
            list_frame, height=min(12, max(4, len(warnings))), width=72
        )
        scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=listbox.yview
        )
        listbox.configure(yscrollcommand=scroll.set)
        listbox.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for line in warnings:
            listbox.insert(tk.END, line)

        btns = ttk.Frame(frm)
        btns.grid(row=row + 1, column=0, sticky="e", pady=(12, 0))

        def close() -> None:
            dlg.destroy()

        ttk.Button(btns, text="OK", command=close).pack(side=tk.RIGHT)
        dlg.bind("<Return>", lambda _e: close())
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.wait_window()

    def _show_done_dialog(self, message: str, open_dir: Path | None) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Done")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        frm = ttk.Frame(dlg, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frm, text=message, justify=tk.LEFT, wraplength=480).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(16, 0))

        def close() -> None:
            dlg.destroy()

        def open_folder() -> None:
            if open_dir is not None:
                open_in_finder(open_dir)
            close()

        def open_guide() -> None:
            close()
            self._show_usage_guide()

        if open_dir is not None:
            ttk.Button(btns, text="Open folder", command=open_folder).pack(
                side=tk.LEFT, padx=(0, 8)
            )
        ttk.Button(btns, text="Open usage guide", command=open_guide).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btns, text="OK", command=close).pack(side=tk.LEFT)
        dlg.bind("<Return>", lambda _e: close())
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.wait_window()

    def _start_update_check(self, *, manual: bool) -> None:
        def worker() -> None:
            result = check_for_update(__version__)
            self._ui(lambda r=result, m=manual: self._handle_update_check_result(r, manual=m))

        threading.Thread(target=worker, daemon=True).start()

    def _check_for_updates_manual(self) -> None:
        self._start_update_check(manual=True)

    def _handle_update_check_result(
        self, result: UpdateCheckResult, *, manual: bool
    ) -> None:
        if result.is_error:
            if manual:
                messagebox.showerror(
                    "Update check failed",
                    f"Could not check for updates:\n\n{result.message}",
                )
            return
        if result.is_up_to_date:
            if manual:
                messagebox.showinfo(
                    "No updates",
                    f"Rekordbox Playlist Converter {__version__} is up to date.",
                )
            return
        if result.is_update_available and result.release is not None:
            if manual or not self._update_modal_shown:
                if not manual:
                    self._update_modal_shown = True
                self._show_update_available(result.release)

    def _show_update_available(self, release: ReleaseInfo) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Update available")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        frm = ttk.Frame(dlg, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")

        message = (
            f"A new version is available.\n\n"
            f"Current version: {__version__}\n"
            f"Latest version: {release.version}"
        )
        if release.release_notes:
            message += f"\n\n{release.release_notes}"
        ttk.Label(frm, text=message, justify=tk.LEFT, wraplength=480).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(16, 0))

        def close() -> None:
            dlg.destroy()

        def view_release() -> None:
            webbrowser.open(release.html_url)
            close()

        ttk.Button(btns, text="Later", command=close).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="View release", command=view_release).pack(side=tk.LEFT)
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.wait_window()


def main() -> int:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
