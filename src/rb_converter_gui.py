#!/usr/bin/env python3
"""Tiny tkinter front-end for Simple Rekordbox Converter."""

from __future__ import annotations

import sys

from gui_preferences import (
    DOCUMENTS_PROBE_FLAG,
    FIND_REKORDBOX_XML_FLAG,
    IMPORT_XML_NAME,
    default_output_paths,
    find_rekordbox_xml_via_child,
    import_xml_path,
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
import time
import tkinter as tk
import webbrowser
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import rb_playlist_to_wav as rb
import converter_manifest
from preview_bit_depth import (
    cached_preview_bit_depth,
    peek_cached_preview_bit_depth,
)
from update_check import ReleaseInfo, UpdateCheckResult, check_for_update
from usage_guide import USAGE_GUIDE
from version import __version__

DEFAULT_WAV_DIR, DEFAULT_OUTPUT = default_output_paths(documents_accessible=True)
FALLBACK_WAV_DIR, FALLBACK_OUTPUT = default_output_paths(documents_accessible=False)
SEARCH_PLACEHOLDER = "Search playlists…"
TRACK_SEARCH_PLACEHOLDER = "Search tracks…"
SCANNING_BIT_DEPTH = "Scanning bit depth…"

# Tracklist playlist-group highlight when any of its tracks are selected
# (darker than the Treeview selection blue used on leaf rows).
TRACKLIST_HEADER_TAG = "playlist_header"
TRACKLIST_HEADER_SELECTED_TAG = "playlist_header_selected"
TRACKLIST_HEADER_SELECTED_BG = "#0a2f55"
TRACKLIST_HEADER_SELECTED_FG = "#ffffff"
APP_LOGO_NAME = "rpc-logo-white.png"
XML_SEARCH_TIMEOUT_SECONDS = 30.0
APP_WINDOW_ICON_NAME = "rpc-logo-white-256.png"
BIT_DEPTH_24_TOOLTIP = (
    "This is a maximum, not a target. "
    "16-bit tracks are not upconverted to 24-bit."
)
SAMPLE_RATE_48_TOOLTIP = (
    "This is a maximum, not a target. "
    "44.1 kHz tracks are not upconverted to 48 kHz."
)
BIT_DEPTH_LABELS = {"16": "16-bit", "24": "24-bit"}
SAMPLE_RATE_LABELS = {"44100": "44.1 kHz", "48000": "48 kHz"}
BIT_DEPTH_FROM_LABEL = {label: value for value, label in BIT_DEPTH_LABELS.items()}
SAMPLE_RATE_FROM_LABEL = {label: value for value, label in SAMPLE_RATE_LABELS.items()}
ACTION_BUTTON_WIDTH = 9
CANCELLED_STATUS_CLEAR_MS = 3000
PREVIEW_ACTION_LABELS = {
    "reuse": "Reuse existing",
    "copy": "Copy",
    "transcode": "Transcode",
}


@dataclass
class PreparedConversion:
    """In-memory prepare result held until the user confirms or discards."""

    plans: list
    items: list
    manifest: converter_manifest.ConverterManifest
    preview: rb.ConversionPreview
    wav_dir: Path
    output: Path
    skipped: list[str]
SEARCH_DEBOUNCE_MS = 200
WAV_DIR_VALIDATE_DEBOUNCE_MS = 300
# One probe worker; apply this many depths per UI callback so Tk can paint.
PREVIEW_BIT_DEPTH_BATCH = 24
PREVIEW_BIT_DEPTH_YIELD_S = 0.02


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


def progress_action_status_hint(
    action: str, current: int, total: int, name: str
) -> str:
    """Status line with counter after the verb: Convert (n/m) TrackName…"""
    return f"{action.capitalize()} ({current}/{total}) {name}…"


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
    """Reveal a folder or select a file in Finder (macOS) / file browser."""
    if path.is_dir():
        if not path.exists():
            return
        subprocess.run(["open", str(path)], check=False)
        return
    if path.exists():
        subprocess.run(["open", "-R", str(path)], check=False)
        return
    parent = path.parent
    if parent.exists():
        subprocess.run(["open", str(parent)], check=False)


class _SearchPlaceholder:
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


class ConverterApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        documents_accessible: bool | None = None,
    ) -> None:
        self.root = root
        root.title(f"Simple Rekordbox Converter {__version__}")
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
        startup_wav, _startup_output = resolve_startup_paths(
            saved_prefs,
            default_wav_dir=FALLBACK_WAV_DIR,
            default_import_xml=FALLBACK_OUTPUT,
            documents_accessible=False,
        )
        self.wav_dir_var.set(str(startup_wav))
        self._sync_import_xml_display()
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
        self.track_search_var = tk.StringVar()
        self._playlist_search = _SearchPlaceholder(self.search_var, SEARCH_PLACEHOLDER)
        self._track_search = _SearchPlaceholder(
            self.track_search_var, TRACK_SEARCH_PLACEHOLDER
        )
        self.status_var = tk.StringVar(value="Choose a Rekordbox XML export.")
        self.scan_status_var = tk.StringVar(value="")
        self._busy = False
        self._cancel_event = threading.Event()
        self._prepared_conversion: PreparedConversion | None = None
        self._write_prepared: PreparedConversion | None = None
        self._preview_dialog: tk.Toplevel | None = None
        self._usage_window: tk.Toplevel | None = None
        self._update_modal_shown = False
        self._update_check_running = False
        self._progress_target = 0.0
        self._progress_anim_id: str | None = None
        self._cancelled_clear_id: str | None = None
        self._copy_status_clear_id: str | None = None
        self._documents_accessible = False
        self._source_root = None
        self._collection_indexes_cache: tuple[dict, dict] | None = None
        # (kind, folder, name, track_count, node) for every node in the XML walk
        self._playlist_entries: list[tuple[str, str, str, int, object]] = []
        # iid -> (kind, folder, name) for rows currently in the tree
        self._playlist_iids: dict[str, tuple[str, str, str]] = {}
        # leaf iid -> (folder, name, key); group iids are absent
        self._tracklist_iids: dict[str, tuple[str, str, str]] = {}
        self._tracklist_paths: dict[str, Path] = {}
        # (folder, name) -> open state for tracklist playlist groups
        self._tracklist_group_open: dict[tuple[str, str], bool] = {}
        self._tracklist_group_iids: dict[str, tuple[str, str]] = {}
        self._tracklist_selecting = False
        self._playlist_selecting = False
        self._tracklist_tech_gen = 0
        self._preview_bit_depth_cache: dict = {}
        self._preview_bit_depth_lock = threading.Lock()
        self._preview_probe_thread: threading.Thread | None = None
        self._preview_scan_active = False
        self._browser_sash_set = False
        self._tracklist_sort_column: str | None = None
        self._tracklist_sort_reverse = False
        self._playlist_search_after_id: str | None = None
        self._track_search_after_id: str | None = None
        self._wav_dir_validate_after_id: str | None = None
        self._wav_dir_validate_gen = 0
        self._wav_dir_valid = False
        self._wav_dir_checking = False
        self.wav_dir_error_var = tk.StringVar(value="")

        self._build()
        self.search_var.trace_add(
            "write",
            lambda *_: self._debounce(
                "_playlist_search_after_id", self._apply_playlist_filter
            ),
        )
        self.track_search_var.trace_add(
            "write",
            lambda *_: self._debounce(
                "_track_search_after_id", self._refresh_tracklist_preview
            ),
        )
        self.wav_dir_var.trace_add(
            "write",
            lambda *_: self._on_wav_dir_changed(),
        )
        if not self.search_var.get():
            self._playlist_search.show()
        if not self.track_search_var.get():
            self._track_search.show()
        self._schedule_wav_dir_validation()
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
        self._place_dialog_over_app(dlg)
        dlg.wait_window()

    def _place_dialog_over_app(self, dlg: tk.Toplevel) -> None:
        dlg.update_idletasks()
        dlg.geometry(
            center_over_window_geometry(
                self.root.winfo_rootx(),
                self.root.winfo_rooty(),
                max(self.root.winfo_width(), 1),
                max(self.root.winfo_height(), 1),
                max(dlg.winfo_reqwidth(), dlg.winfo_width(), 1),
                max(dlg.winfo_reqheight(), dlg.winfo_height(), 1),
            )
        )

    def _apply_documents_access(self, override: bool | None) -> None:
        if override is None:
            accessible = False
        else:
            accessible = override
        self._documents_accessible = accessible
        if accessible:
            saved = load_preferences()
            docs_wav, docs_xml = default_output_paths(documents_accessible=True)
            startup_wav, _startup_output = resolve_startup_paths(
                saved,
                default_wav_dir=docs_wav,
                default_import_xml=docs_xml,
                documents_accessible=True,
            )
            self.wav_dir_var.set(str(startup_wav))
            self._schedule_wav_dir_validation()
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
        frm.rowconfigure(1, weight=1)

        ttk.Label(frm, text="Rekordbox XML").grid(row=0, column=0, sticky="w", **pad)
        self.xml_entry = ttk.Entry(frm, textvariable=self.xml_var)
        self.xml_entry.grid(row=0, column=1, sticky="ew", **pad)
        xml_btns = ttk.Frame(frm)
        xml_btns.grid(row=0, column=2, sticky="e", **pad)
        self.xml_browse_btn = ttk.Button(
            xml_btns,
            text="Browse…",
            width=ACTION_BUTTON_WIDTH,
            command=self._browse_xml,
        )
        self.xml_browse_btn.pack(side=tk.LEFT)
        self.refresh_btn = ttk.Button(
            xml_btns, text="Refresh", command=self._refresh_xml
        )
        self.refresh_btn.pack(side=tk.LEFT, padx=(4, 0))

        list_frame = ttk.Frame(frm)
        list_frame.grid(row=1, column=0, columnspan=3, sticky="nsew", **pad)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        panes = ttk.Panedwindow(list_frame, orient=tk.HORIZONTAL)
        panes.grid(row=0, column=0, sticky="nsew")
        self.browser_panes = panes
        panes.bind("<Configure>", self._on_browser_panes_configure, add="+")

        left = ttk.Frame(panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        self.search_entry = ttk.Entry(left, textvariable=self.search_var)
        self.search_entry.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._playlist_search.bind(self.search_entry)
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
        self.playlist_tree.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.playlist_tree.bind("<<TreeviewSelect>>", self._on_playlist_select, add="+")
        self.playlist_tree.bind("<Button-1>", self._on_playlist_button1, add="+")

        right = ttk.Frame(panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self.track_search_entry = ttk.Entry(right, textvariable=self.track_search_var)
        self.track_search_entry.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._track_search.bind(self.track_search_entry)
        self.tracklist_tree = ttk.Treeview(
            right,
            columns=("format", "bit_depth", "sample_rate"),
            show="tree headings",
            selectmode="extended",
            height=12,
        )
        self.tracklist_tree.heading(
            "#0", text="Track", anchor="w", command=lambda: self._on_tracklist_sort("#0")
        )
        self.tracklist_tree.heading(
            "format",
            text="Format",
            anchor="w",
            command=lambda: self._on_tracklist_sort("format"),
        )
        self.tracklist_tree.heading(
            "bit_depth",
            text="Bit depth",
            anchor="w",
            command=lambda: self._on_tracklist_sort("bit_depth"),
        )
        self.tracklist_tree.heading(
            "sample_rate",
            text="Sample rate",
            anchor="w",
            command=lambda: self._on_tracklist_sort("sample_rate"),
        )
        self.tracklist_tree.column("#0", stretch=True, minwidth=120)
        self.tracklist_tree.column("format", width=70, stretch=False, anchor="center")
        self.tracklist_tree.column(
            "bit_depth", width=90, stretch=False, anchor="center"
        )
        self.tracklist_tree.column(
            "sample_rate", width=90, stretch=False, anchor="center"
        )
        self.tracklist_tree.tag_configure(
            TRACKLIST_HEADER_SELECTED_TAG,
            background=TRACKLIST_HEADER_SELECTED_BG,
            foreground=TRACKLIST_HEADER_SELECTED_FG,
        )
        track_scroll = ttk.Scrollbar(
            right, orient=tk.VERTICAL, command=self.tracklist_tree.yview
        )
        self.tracklist_tree.configure(yscrollcommand=track_scroll.set)
        self.tracklist_tree.grid(row=1, column=0, sticky="nsew")
        track_scroll.grid(row=1, column=1, sticky="ns")
        self.tracklist_tree.bind(
            "<<TreeviewSelect>>", self._on_tracklist_select, add="+"
        )
        self.tracklist_tree.bind("<Button-1>", self._on_tracklist_button1, add="+")
        self.tracklist_tree.bind(
            "<<TreeviewOpen>>", self._remember_tracklist_group_open, add="+"
        )
        self.tracklist_tree.bind(
            "<<TreeviewClose>>", self._remember_tracklist_group_open, add="+"
        )

        panes.add(left, weight=1)
        panes.add(right, weight=1)
        self._refresh_tracklist_preview()

        ttk.Label(frm, text="Output folder").grid(row=2, column=0, sticky="w", **pad)
        self.wav_dir_entry = ttk.Entry(frm, textvariable=self.wav_dir_var)
        self.wav_dir_entry.grid(row=2, column=1, sticky="ew", **pad)
        self.wav_dir_browse_btn = ttk.Button(
            frm,
            text="Browse…",
            width=ACTION_BUTTON_WIDTH,
            command=self._browse_wav_dir,
        )
        self.wav_dir_browse_btn.grid(row=2, column=2, sticky="e", **pad)
        self.wav_dir_error_label = ttk.Label(
            frm,
            textvariable=self.wav_dir_error_var,
            foreground="#a40000",
            wraplength=720,
        )
        # Row 3 is used only while validation has a message (grid_remove otherwise).

        import_xml_label = ttk.Label(frm, text="Import XML")
        import_xml_label.grid(row=4, column=0, sticky="w", **pad)
        import_xml_label.configure(cursor="hand2")
        import_xml_label.bind("<Button-1>", self._copy_import_xml_path, add="+")
        self.import_xml_entry = ttk.Entry(
            frm, textvariable=self.output_var, state="disabled", cursor="hand2"
        )
        self.import_xml_entry.grid(row=4, column=1, sticky="ew", **pad)
        self.import_xml_entry.bind(
            "<Button-1>", self._copy_import_xml_path, add="+"
        )
        _HoverTooltip(self.import_xml_entry, "Click to copy the Import XML path")

        ttk.Label(frm, text="Format").grid(row=5, column=0, sticky="w", **pad)
        format_opts = ttk.Frame(frm)
        format_opts.grid(row=5, column=1, sticky="w", **pad)
        self.format_wav_radio = ttk.Radiobutton(
            format_opts,
            text="WAV",
            variable=self.format_var,
            value="wav",
            command=self._persist_output_preferences,
        )
        self.format_wav_radio.pack(side=tk.LEFT)
        self.format_aiff_radio = ttk.Radiobutton(
            format_opts,
            text="AIFF",
            variable=self.format_var,
            value="aiff",
            command=self._persist_output_preferences,
        )
        self.format_aiff_radio.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frm, text="Max. quality").grid(row=6, column=0, sticky="w", **pad)
        quality = ttk.Frame(frm)
        quality.grid(row=6, column=1, sticky="w", **pad)
        self.bit_depth_combo = ttk.Combobox(
            quality,
            values=list(BIT_DEPTH_LABELS.values()),
            state="readonly",
            width=8,
        )
        self.bit_depth_combo.set(
            BIT_DEPTH_LABELS.get(self.bit_depth_var.get(), "24-bit")
        )
        self.bit_depth_combo.pack(side=tk.LEFT)
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
            SAMPLE_RATE_LABELS.get(self.sample_rate_var.get(), "48 kHz")
        )
        self.sample_rate_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.sample_rate_combo.bind(
            "<<ComboboxSelected>>", self._on_sample_rate_selected, add="+"
        )
        _HoverTooltip(self.sample_rate_combo, SAMPLE_RATE_48_TOOLTIP)

        self.progress = ttk.Progressbar(frm, mode="determinate", maximum=100)
        self.progress.grid(row=7, column=0, columnspan=2, sticky="ew", **pad)
        self.progress["value"] = 0
        self.convert_btn = ttk.Button(
            frm,
            text="Convert",
            width=ACTION_BUTTON_WIDTH,
            command=self._start_convert,
        )
        self.convert_btn.grid(row=7, column=2, sticky="e", **pad)
        self.cancel_btn = ttk.Button(
            frm,
            text="Cancel",
            width=ACTION_BUTTON_WIDTH,
            command=self._request_cancel,
            state=tk.DISABLED,
        )
        self.cancel_btn.grid(row=7, column=2, sticky="e", **pad)
        self.cancel_btn.grid_remove()

        status_row = ttk.Frame(frm)
        status_row.grid(row=8, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(status_row, textvariable=self.status_var, wraplength=1000).pack(
            side=tk.LEFT
        )
        ttk.Label(status_row, textvariable=self.scan_status_var).pack(
            side=tk.LEFT, padx=(12, 0)
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

        def worker() -> None:
            hits = find_rekordbox_xml_via_child(
                Path.home(),
                timeout_seconds=XML_SEARCH_TIMEOUT_SECONDS,
            )

            def apply() -> None:
                if len(hits) == 1:
                    self._adopt_source_xml(hits[0])
                    self._persist_output_preferences(include_source_xml=True)
                elif len(hits) >= 2:
                    self._show_xml_choice_modal(hits)

            self._ui(apply)

        threading.Thread(target=worker, daemon=True).start()

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
        self._place_dialog_over_app(dlg)
        dlg.focus_force()

    def _on_browser_panes_configure(self, event: object = None) -> None:
        if self._browser_sash_set:
            return
        widget = getattr(event, "widget", None) or self.browser_panes
        try:
            width = int(widget.winfo_width())
        except tk.TclError:
            return
        if width <= 1:
            return
        try:
            self.browser_panes.sashpos(0, round(width * 0.3))
        except tk.TclError:
            return
        self._browser_sash_set = True

    def _on_bit_depth_selected(self, _event: object = None) -> None:
        label = self.bit_depth_combo.get().strip()
        self.bit_depth_var.set(BIT_DEPTH_FROM_LABEL.get(label, "24"))
        self._persist_output_preferences()

    def _on_sample_rate_selected(self, _event: object = None) -> None:
        label = self.sample_rate_combo.get().strip()
        self.sample_rate_var.set(SAMPLE_RATE_FROM_LABEL.get(label, "48000"))
        self._persist_output_preferences()

    def _on_wav_dir_changed(self) -> None:
        self._sync_import_xml_display()
        self._schedule_wav_dir_validation()

    def _resolved_output_paths(self) -> tuple[Path, Path]:
        wav_dir = Path(
            self.wav_dir_var.get().strip() or str(DEFAULT_WAV_DIR)
        ).expanduser()
        if not wav_dir.is_absolute():
            wav_dir = Path.home() / wav_dir
        return wav_dir, import_xml_path(wav_dir)

    def _sync_import_xml_display(self) -> None:
        _wav_dir, output = self._resolved_output_paths()
        self.output_var.set(str(output))

    def _copy_import_xml_path(self, _event: object = None) -> str | None:
        path = str(self._resolved_output_paths()[1])
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            self.root.update_idletasks()
        except tk.TclError:
            return "break"
        if not self._busy:
            self._cancel_copy_status_clear()
            self.status_var.set(f"Copied path: {path}")
            self._copy_status_clear_id = self.root.after(
                CANCELLED_STATUS_CLEAR_MS, self._clear_copy_status
            )
        return "break"

    def _cancel_copy_status_clear(self) -> None:
        if self._copy_status_clear_id is not None:
            self.root.after_cancel(self._copy_status_clear_id)
            self._copy_status_clear_id = None

    def _clear_copy_status(self) -> None:
        self._copy_status_clear_id = None
        if self._busy:
            return
        if not self.status_var.get().startswith("Copied path:"):
            return
        self._set_idle_status(self._tracklist_selection_summary())

    def _persist_output_preferences(self, *, include_source_xml: bool = False) -> None:
        wav_dir, _output = self._resolved_output_paths()
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
        if self._busy:
            return
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
        if self._busy:
            return
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
            self._schedule_wav_dir_validation()

    def _debounce(self, attr: str, callback) -> None:
        prev = getattr(self, attr)
        if prev is not None:
            self.root.after_cancel(prev)

        def fire() -> None:
            setattr(self, attr, None)
            callback()

        setattr(self, attr, self.root.after(SEARCH_DEBOUNCE_MS, fire))

    def _load_playlists(self) -> None:
        self.playlist_tree.delete(*self.playlist_tree.get_children())
        self._playlist_entries = []
        self._playlist_iids = {}
        self._source_root = None
        self._collection_indexes_cache = None
        # Clear under the same lock used by peek/fill so a probe worker cannot
        # race a load that resets the cache.
        with self._preview_bit_depth_lock:
            self._preview_bit_depth_cache.clear()
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            self._refresh_tracklist_preview()
            return
        path = Path(xml_s).expanduser()
        if not path.is_file():
            self._refresh_tracklist_preview()
            self.status_var.set(f"XML not found: {path}")
            return
        try:
            root = rb.load_dj_playlists(path)
        except rb.CliError as exc:
            self._refresh_tracklist_preview()
            self.status_var.set(str(exc))
            return
        self._source_root = root
        self._collection_indexes_cache = rb.collection_indexes(root)
        nodes = rb.iter_playlist_nodes(root)
        by_id, by_location = self._collection_indexes_cache
        for kind, folder, name, node in nodes:
            count = (
                rb.playlist_preview_track_count(
                    node,
                    by_id,
                    by_location,
                    supported_ext=rb.SUPPORTED_LOSSLESS_EXT,
                )
                if kind == "playlist"
                else 0
            )
            self._playlist_entries.append((kind, folder, name, count, node))
        self._playlist_search.show()
        self._track_search.show()
        self._apply_playlist_filter()

    @staticmethod
    def _playlist_iid(kind: str, folder: str, name: str) -> str:
        return f"{kind}:{rb.playlist_label(folder, name)}"

    @staticmethod
    def _playlist_row_text(kind: str, name: str, count: int) -> str:
        if kind == "folder":
            return name
        return f"{name} ({count} tracks)"

    def _apply_playlist_filter(self) -> None:
        query = self._playlist_search.query().casefold()
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

    def _on_playlist_button1(self, event: object) -> str | None:
        """Folder row clicks only expand/collapse; playlists keep normal select."""
        if self._busy:
            return None
        tree = self.playlist_tree
        y = getattr(event, "y", None)
        if y is None:
            return None
        row = tree.identify_row(y)
        if not row:
            return None
        meta = self._playlist_iids.get(row)
        if meta is None or meta[0] != "folder":
            return None
        tree.item(row, open=not bool(tree.item(row, "open")))
        return "break"

    def _on_playlist_select(self, _event: object = None) -> None:
        if self._busy or self._playlist_selecting:
            return
        tree = self.playlist_tree
        keep = [
            iid
            for iid in tree.selection()
            if (meta := self._playlist_iids.get(iid)) is not None
            and meta[0] == "playlist"
        ]
        if set(keep) != set(tree.selection()):
            self._playlist_selecting = True
            try:
                tree.selection_set(keep)
            finally:
                self._playlist_selecting = False
        self._refresh_tracklist_preview()

    def _on_tracklist_button1(self, event: object) -> str | None:
        """Expand/collapse arrow on a playlist group must not change selection."""
        if self._busy:
            return None
        tree = self.tracklist_tree
        y = getattr(event, "y", None)
        x = getattr(event, "x", None)
        if y is None or x is None:
            return None
        row = tree.identify_row(y)
        if not row or row not in self._tracklist_group_iids:
            return None
        element = tree.identify("element", x, y)
        if element != "Treeitem.indicator":
            return None
        is_open = not bool(tree.item(row, "open"))
        tree.item(row, open=is_open)
        self._tracklist_group_open[self._tracklist_group_iids[row]] = is_open
        return "break"

    def _remember_tracklist_group_open(self, _event: object = None) -> None:
        iid = self.tracklist_tree.focus()
        key = self._tracklist_group_iids.get(iid)
        if key is not None:
            self._tracklist_group_open[key] = bool(
                self.tracklist_tree.item(iid, "open")
            )

    def _on_tracklist_select(self, _event: object = None) -> None:
        if self._busy or self._tracklist_selecting:
            return
        preview = self.tracklist_tree
        selected = list(preview.selection())
        leaf_iids: list[str] = []
        remapped = False
        for iid in selected:
            if iid in self._tracklist_iids:
                leaf_iids.append(iid)
                continue
            # Group header → that group's track leaves.
            remapped = True
            for child in preview.get_children(iid):
                if child in self._tracklist_iids:
                    leaf_iids.append(child)
        # Preserve order, drop duplicates.
        seen: set[str] = set()
        unique_leaves: list[str] = []
        for iid in leaf_iids:
            if iid not in seen:
                seen.add(iid)
                unique_leaves.append(iid)
        if remapped or set(selected) != set(unique_leaves):
            self._tracklist_selecting = True
            try:
                preview.selection_set(unique_leaves)
            finally:
                self._tracklist_selecting = False
        self._sync_tracklist_header_highlights()
        self._set_idle_status(self._tracklist_selection_summary())

    def _sync_tracklist_header_highlights(self) -> None:
        """Darker blue on playlist group rows when any of their tracks are selected."""
        preview = self.tracklist_tree
        selected = set(preview.selection())
        for group_iid in preview.get_children(""):
            children = preview.get_children(group_iid)
            active = bool(children) and any(child in selected for child in children)
            tags = (
                (TRACKLIST_HEADER_TAG, TRACKLIST_HEADER_SELECTED_TAG)
                if active
                else (TRACKLIST_HEADER_TAG,)
            )
            preview.item(group_iid, tags=tags)

    def _tracklist_selection_summary(self) -> str | None:
        unique_keys: set[str] = set()
        playlists: set[tuple[str, str]] = set()
        for iid in self.tracklist_tree.selection():
            meta = self._tracklist_iids.get(iid)
            if meta is None:
                continue
            folder, name, key = meta
            playlists.add((folder, name))
            if key:
                unique_keys.add(key)
        if not unique_keys or not playlists:
            return None
        painted = len(playlists)
        playlist_word = "playlist" if painted == 1 else "playlists"
        return f"{len(unique_keys)} unique tracks from {painted} {playlist_word}"

    def _playlist_node(self, folder: str, name: str):
        for kind, entry_folder, entry_name, _count, node in self._playlist_entries:
            if kind == "playlist" and entry_folder == folder and entry_name == name:
                return node
        return None

    @staticmethod
    def _track_preview_row(track) -> tuple[str, str, str, str]:
        """Return (label, format, bit_depth, sample_rate) from a collection TRACK.

        Bit depth is always — here; file headers are filled asynchronously.
        """
        empty = "—"
        if track is None:
            return "(missing track)", empty, empty, empty
        artist = track.get("Artist") or ""
        title = track.get("Name") or ""
        label = f"{artist} - {title}" if artist else title
        loc = track.get("Location") or ""
        path = rb.decode_location(loc) if loc else None
        if path is not None and path.suffix:
            label = f"{label}{path.suffix.lower()}"
        kind = (track.get("Kind") or "").strip()
        if kind.endswith(" File"):
            fmt = kind[: -len(" File")].strip() or empty
        else:
            fmt = kind or empty
        rate = (track.get("SampleRate") or "").strip() or empty
        return label, fmt, empty, rate

    def _sync_scan_indicator(self) -> None:
        if self._busy or not self._preview_scan_active:
            self.scan_status_var.set("")
            return
        self.scan_status_var.set(SCANNING_BIT_DEPTH)

    def _set_preview_scan_active(self, active: bool) -> None:
        self._preview_scan_active = active
        self._sync_scan_indicator()

    def _refresh_tracklist_preview(self) -> None:
        self._tracklist_tech_gen += 1
        gen = self._tracklist_tech_gen
        self.tracklist_tree.delete(*self.tracklist_tree.get_children())
        self._tracklist_iids = {}
        self._tracklist_paths = {}
        self._tracklist_group_iids = {}
        if self._source_root is None:
            self._tracklist_group_open.clear()
            self._set_preview_scan_active(False)
            self._set_idle_status()
            return
        selected = self._selected_playlists(unique_names=False)
        if not selected:
            self._tracklist_group_open.clear()
            self._set_preview_scan_active(False)
            self._set_idle_status()
            return
        selected_keys = set(selected)
        for key in list(self._tracklist_group_open):
            if key not in selected_keys:
                del self._tracklist_group_open[key]
        query = self._track_search.query().casefold()
        if self._collection_indexes_cache is None:
            self._collection_indexes_cache = rb.collection_indexes(self._source_root)
        by_id, by_location = self._collection_indexes_cache
        leaf_iids: list[str] = []
        painted = 0
        paths: list[Path] = []
        seen_paths: set[Path] = set()
        for folder, name in selected:
            node = self._playlist_node(folder, name)
            if node is None:
                continue
            key_type = node.get("KeyType", "0")
            matched: list[tuple[str, str, tuple[str, str, str], Path | None]] = []
            for entry in node.findall("TRACK"):
                key = entry.get("Key") or ""
                track = None
                if key:
                    if key_type == "1":
                        track = by_location.get(key)
                    else:
                        track = by_id.get(key)
                label, fmt, depth, rate = self._track_preview_row(track)
                if query and query not in label.casefold():
                    continue
                loc = (track.get("Location") or "") if track is not None else ""
                path = rb.decode_location(loc) if loc else None
                if not rb.track_included_in_playlist_preview(
                    track, supported_ext=rb.SUPPORTED_LOSSLESS_EXT
                ):
                    continue
                if path is not None:
                    hit, bits = peek_cached_preview_bit_depth(
                        path,
                        self._preview_bit_depth_cache,
                        lock=self._preview_bit_depth_lock,
                    )
                    if hit:
                        depth = str(bits) if bits is not None else "—"
                    elif path not in seen_paths:
                        seen_paths.add(path)
                        paths.append(path)
                matched.append((key, label, (fmt, depth, rate), path))
            if not matched:
                continue
            group_key = (folder, name)
            is_open = self._tracklist_group_open.setdefault(group_key, True)
            group_text = f"{name} ({len(matched)} tracks)"
            group_iid = self.tracklist_tree.insert(
                "",
                tk.END,
                text=group_text,
                open=is_open,
                values=("", "", ""),
                tags=(TRACKLIST_HEADER_TAG,),
            )
            self._tracklist_group_iids[group_iid] = group_key
            painted += 1
            for key, label, values, path in matched:
                leaf_iid = self.tracklist_tree.insert(
                    group_iid, tk.END, text=label, values=values
                )
                self._tracklist_iids[leaf_iid] = (folder, name, key)
                if path is not None:
                    self._tracklist_paths[leaf_iid] = path
                leaf_iids.append(leaf_iid)
        if not painted:
            self._set_preview_scan_active(False)
            self._set_idle_status()
            return
        self._tracklist_selecting = True
        try:
            if leaf_iids:
                self.tracklist_tree.selection_set(leaf_iids)
        finally:
            self._tracklist_selecting = False
        self._sync_tracklist_header_highlights()
        self._set_idle_status(self._tracklist_selection_summary())
        if self._tracklist_sort_column is not None:
            self._apply_tracklist_sort()
        if paths:
            # Generation already bumped; old worker exits between files.
            self._set_preview_scan_active(True)
            worker = threading.Thread(
                target=lambda: self._fill_preview_bit_depths(gen, paths),
                daemon=True,
            )
            self._preview_probe_thread = worker
            worker.start()
        else:
            self._set_preview_scan_active(False)

    def _on_tracklist_sort(self, column: str) -> None:
        if self._tracklist_sort_column == column:
            self._tracklist_sort_reverse = not self._tracklist_sort_reverse
        else:
            self._tracklist_sort_column = column
            self._tracklist_sort_reverse = False
        self._apply_tracklist_sort()

    def _tracklist_sort_key(self, iid: str, column: str):
        preview = self.tracklist_tree
        if column == "#0":
            return preview.item(iid, "text").casefold()
        values = list(preview.item(iid, "values"))
        idx = {"format": 0, "bit_depth": 1, "sample_rate": 2}.get(column)
        if idx is None or idx >= len(values):
            return ""
        raw = str(values[idx] or "")
        if column in ("bit_depth", "sample_rate"):
            try:
                return (0, int(raw))
            except ValueError:
                return (1, 0)
        return raw.casefold()

    def _apply_tracklist_sort(self) -> None:
        column = self._tracklist_sort_column
        if column is None:
            return
        preview = self.tracklist_tree
        reverse = self._tracklist_sort_reverse
        for group_iid in preview.get_children(""):
            leaves = list(preview.get_children(group_iid))
            if column in ("bit_depth", "sample_rate"):
                numbered: list[tuple[int, str]] = []
                empty: list[str] = []
                for iid in leaves:
                    key = self._tracklist_sort_key(iid, column)
                    if isinstance(key, tuple) and key[0] == 0:
                        numbered.append((key[1], iid))
                    else:
                        empty.append(iid)
                numbered.sort(key=lambda pair: pair[0], reverse=reverse)
                ordered = [iid for _, iid in numbered] + empty
            else:
                ordered = sorted(
                    leaves,
                    key=lambda iid: self._tracklist_sort_key(iid, column),
                    reverse=reverse,
                )
            for index, iid in enumerate(ordered):
                preview.move(iid, group_iid, index)

    def _fill_preview_bit_depths(self, gen: int, paths: list[Path]) -> None:
        batch: dict[Path, str] = {}

        def flush() -> None:
            if not batch or gen != self._tracklist_tech_gen:
                batch.clear()
                return
            snapshot = dict(batch)
            batch.clear()
            self._ui(lambda b=snapshot, g=gen: self._apply_preview_bit_depths(g, b))

        for path in paths:
            if gen != self._tracklist_tech_gen:
                return
            try:
                bits = cached_preview_bit_depth(
                    path,
                    self._preview_bit_depth_cache,
                    lock=self._preview_bit_depth_lock,
                )
            except Exception:
                continue
            if bits is not None:
                batch[path] = str(bits)
            if len(batch) >= PREVIEW_BIT_DEPTH_BATCH:
                flush()
                time.sleep(PREVIEW_BIT_DEPTH_YIELD_S)
        flush()
        if gen == self._tracklist_tech_gen:
            self._ui(lambda g=gen: self._finish_preview_bit_depth_scan(g))

    def _finish_preview_bit_depth_scan(self, gen: int) -> None:
        if gen != self._tracklist_tech_gen:
            return
        self._set_preview_scan_active(False)

    def _apply_preview_bit_depths(self, gen: int, depths: dict[Path, str]) -> None:
        if gen != self._tracklist_tech_gen or not depths:
            return
        for iid, path in self._tracklist_paths.items():
            depth = depths.get(path)
            if depth is None:
                continue
            values = list(self.tracklist_tree.item(iid, "values"))
            if len(values) < 3:
                continue
            values[1] = depth
            self.tracklist_tree.item(iid, values=values)
        if self._tracklist_sort_column == "bit_depth":
            self._apply_tracklist_sort()

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

        for iid in self.playlist_tree.selection():
            collect_from_iid(iid)

        if unique_names:
            names = [name for _folder, name in chosen]
            # Same output playlist name `{name} [WAV]` — refuse converting two at once.
            dupes = {n for n, c in Counter(names).items() if c > 1}
            if dupes:
                listed = ", ".join(sorted(dupes))
                raise rb.CliError(
                    f"cannot select multiple playlists with the same name: {listed}"
                )
        return chosen

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        edit_state = tk.DISABLED if busy else tk.NORMAL
        combo_state = "disabled" if busy else "readonly"
        tree_state = ("disabled",) if busy else ("!disabled",)
        self.xml_entry.configure(state=edit_state)
        self.wav_dir_entry.configure(state=edit_state)
        self.xml_browse_btn.configure(state=edit_state)
        self.wav_dir_browse_btn.configure(state=edit_state)
        self.search_entry.configure(state=edit_state)
        self.track_search_entry.configure(state=edit_state)
        self.format_wav_radio.configure(state=edit_state)
        self.format_aiff_radio.configure(state=edit_state)
        self.bit_depth_combo.configure(state=combo_state)
        self.sample_rate_combo.configure(state=combo_state)
        self.refresh_btn.configure(state=edit_state)
        self.playlist_tree.state(tree_state)
        self.tracklist_tree.state(tree_state)
        if busy:
            self._cancel_cancelled_clear()
            self._cancel_progress_anim()
            self._progress_target = 0.0
            self.progress["value"] = 0
            self.convert_btn.grid_remove()
            self.cancel_btn.configure(state=tk.NORMAL)
            self.cancel_btn.grid()
            self._sync_scan_indicator()
        else:
            self.cancel_btn.grid_remove()
            self.cancel_btn.configure(state=tk.DISABLED)
            self.convert_btn.grid()
            self._update_convert_enabled()
            self._sync_scan_indicator()

    def _update_convert_enabled(self) -> None:
        if self._busy:
            return
        enabled = self._wav_dir_valid and not self._wav_dir_checking
        self.convert_btn.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_wav_dir_error(self, message: str) -> None:
        self.wav_dir_error_var.set(message)
        if message:
            self.wav_dir_error_label.grid(
                row=3, column=1, columnspan=2, sticky="w", padx=10, pady=(0, 4)
            )
        else:
            self.wav_dir_error_label.grid_remove()

    def _schedule_wav_dir_validation(self) -> None:
        prev = self._wav_dir_validate_after_id
        if prev is not None:
            self.root.after_cancel(prev)
        self._wav_dir_checking = True
        self._wav_dir_valid = False
        self._set_wav_dir_error("")
        self._update_convert_enabled()

        def fire() -> None:
            self._wav_dir_validate_after_id = None
            self._start_wav_dir_validation()

        self._wav_dir_validate_after_id = self.root.after(
            WAV_DIR_VALIDATE_DEBOUNCE_MS, fire
        )

    def _start_wav_dir_validation(self) -> None:
        wav_dir, _output = self._resolved_output_paths()
        self._wav_dir_validate_gen += 1
        gen = self._wav_dir_validate_gen
        self._wav_dir_checking = True
        self._wav_dir_valid = False
        # Do not show a temporary "Checking…" in the error row — it flickers
        # and then hides for valid folders. Convert stays disabled via
        # _wav_dir_checking until the result lands.
        self._update_convert_enabled()

        def worker() -> None:
            error = converter_manifest.validate_library_folder(wav_dir)

            def on_ui() -> None:
                if gen != self._wav_dir_validate_gen:
                    return
                self._wav_dir_checking = False
                if error:
                    self._wav_dir_valid = False
                    self._set_wav_dir_error(error)
                else:
                    self._wav_dir_valid = True
                    self._set_wav_dir_error("")
                self._update_convert_enabled()

            self._ui(on_ui)

        threading.Thread(target=worker, daemon=True).start()

    def _request_cancel(self) -> None:
        if not self._busy:
            return
        if self._prepared_conversion is not None and self._write_prepared is None:
            self._discard_prepared_conversion()
            return
        self._cancel_event.set()
        self.status_var.set("Cancelling…")
        self.cancel_btn.configure(state=tk.DISABLED)

    def _cancel_cancelled_clear(self) -> None:
        if self._cancelled_clear_id is not None:
            self.root.after_cancel(self._cancelled_clear_id)
            self._cancelled_clear_id = None

    def _set_idle_status(self, unique_summary: str | None = None) -> None:
        if self._busy:
            return
        if unique_summary:
            self.status_var.set(unique_summary)
            return
        playlist_count = sum(
            1 for kind, *_rest in self._playlist_entries if kind == "playlist"
        )
        if playlist_count:
            self.status_var.set(
                f"Loaded {playlist_count} playlist(s). Select and Convert."
            )
            return
        if self._source_root is not None:
            self.status_var.set("No playlists found in XML.")
            return
        self.status_var.set("Choose a Rekordbox XML export.")

    def _clear_cancelled_status(self) -> None:
        self._cancelled_clear_id = None
        if self._busy or self.status_var.get() != "Cancelled.":
            return
        self._animate_progress_to(0, snap=True)
        self._refresh_tracklist_preview()

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
            self.status_var.set(
                progress_action_status_hint(action, current, total, name)
            )
        elif total > 0:
            self.status_var.set(f"Working… {current}/{total} ({int(pct)}%)")

    def _ui(self, fn) -> None:
        try:
            self.root.after(0, fn)
        except tk.TclError:
            pass

    def _start_convert(self) -> None:
        if self._busy:
            return
        if not self._wav_dir_valid or self._wav_dir_checking:
            return
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            messagebox.showerror("Missing XML", "Choose a Rekordbox XML export.")
            return
        try:
            selected = self._selected_playlists(unique_names=False)
        except rb.CliError as exc:
            messagebox.showerror("Selection", str(exc))
            return
        if not selected:
            messagebox.showerror("Selection", "Select at least one playlist.")
            return

        keys_by_playlist: dict[tuple[str, str], list[str]] = {}
        for iid in self.tracklist_tree.selection():
            meta = self._tracklist_iids.get(iid)
            if meta is None:
                continue
            folder, name, key = meta
            if not key:
                continue
            keys_by_playlist.setdefault((folder, name), []).append(key)
        selected = [
            (folder, name)
            for folder, name in selected
            if keys_by_playlist.get((folder, name))
        ]
        if not selected:
            messagebox.showerror("Selection", "Select at least one track.")
            return
        names = [name for _folder, name in selected]
        dupes = {n for n, c in Counter(names).items() if c > 1}
        if dupes:
            listed = ", ".join(sorted(dupes))
            messagebox.showerror(
                "Selection",
                f"cannot select multiple playlists with the same name: {listed}",
            )
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
        self._cancel_event.clear()
        self.status_var.set("Preparing…")

        self._convert_selected = selected
        self._convert_keys_by_playlist = keys_by_playlist
        self._convert_wav_dir = wav_dir
        self._convert_output = output
        self._convert_output_format = output_format
        self._convert_max_bit_depth = max_bit_depth
        self._convert_max_sample_rate = max_sample_rate
        self._convert_xml_path = xml_path
        threading.Thread(target=self._prepare_worker, daemon=True).start()

    def _prepare_worker(self) -> None:
        selected = self._convert_selected
        keys_by_playlist = self._convert_keys_by_playlist
        wav_dir = self._convert_wav_dir
        output = self._convert_output
        output_format = self._convert_output_format
        max_bit_depth = self._convert_max_bit_depth
        max_sample_rate = self._convert_max_sample_rate
        xml_path = self._convert_xml_path
        try:
            skipped: list[str] = []
            plans: list[rb.Plan] = []
            # Reuse UI-loaded tree when it matches this convert's XML; else parse once.
            loaded_xml = Path(self.xml_var.get().strip()).expanduser()
            if self._source_root is not None and loaded_xml == xml_path:
                source_root = self._source_root
            elif xml_path.is_file():
                try:
                    source_root = rb.load_dj_playlists(xml_path)
                except rb.CliError as exc:
                    self._ui(lambda e=[str(exc)]: self._finish_error(e))
                    return
            else:
                source_root = None
            try:
                manifest = converter_manifest.load_manifest(wav_dir)
            except rb.CliError as exc:
                self._ui(lambda e=[str(exc)]: self._finish_error(e))
                return
            for i, (folder, name) in enumerate(selected):
                if self._cancel_event.is_set():
                    self._ui(self._finish_cancelled)
                    return
                label = f"{name} ({i + 1}/{len(selected)})"
                self._ui(lambda l=label: self.status_var.set(f"Preparing {l}…"))

                def prepare_tick(
                    current: int,
                    total: int,
                    action: str,
                    track_name: str,
                ) -> None:
                    self._ui(
                        lambda c=current, t=total, a=action, n=track_name: self._set_progress(
                            c, t, action=a, name=n
                        )
                    )

                plan, errors = rb.prepare(
                    xml_path,
                    name,
                    wav_dir,
                    output,
                    playlist_folder=folder,
                    output_format=output_format,
                    max_bit_depth=max_bit_depth,
                    max_sample_rate=max_sample_rate,
                    track_keys=keys_by_playlist[(folder, name)],
                    on_progress=prepare_tick,
                    cancel_event=self._cancel_event,
                    source_root=source_root,
                    manifest=manifest,
                )
                if self._cancel_event.is_set():
                    self._ui(self._finish_cancelled)
                    return
                if errors:
                    self._ui(lambda e=errors: self._finish_error(e))
                    return
                assert plan is not None
                plans.append(plan)
                skipped.extend(plan.warnings)

            rb.share_output_root(plans)
            items = rb.collect_batch_unique(plans)
            rb.share_cover_caches(plans)
            if self._cancel_event.is_set():
                self._ui(self._finish_cancelled)
                return
            preview = rb.build_conversion_preview(plans, items, force=False)
            prepared = PreparedConversion(
                plans=plans,
                items=items,
                manifest=manifest,
                preview=preview,
                wav_dir=wav_dir,
                output=output,
                skipped=skipped,
            )
            self._ui(lambda p=prepared: self._on_prepare_ready(p))
        except rb.CliError as exc:
            self._ui(lambda e=str(exc): self._finish_error(e))
        except Exception as exc:  # noqa: BLE001 — show unexpected errors in UI
            self._ui(lambda e=str(exc): self._finish_error(e))

    def _on_prepare_ready(self, prepared: PreparedConversion) -> None:
        if self._cancel_event.is_set():
            self._finish_cancelled()
            return
        self._prepared_conversion = prepared
        self._show_conversion_preview(prepared)

    def _show_conversion_preview(self, prepared: PreparedConversion) -> None:
        """Modal unique-output preview; Convert continues, Back/Escape discard."""
        self._close_preview_dialog()
        preview = prepared.preview
        dlg = tk.Toplevel(self.root)
        self._preview_dialog = dlg
        dlg.title("Conversion preview")
        dlg.transient(self.root)
        dlg.geometry("960x540")
        dlg.minsize(960, 540)
        dlg.resizable(True, True)

        frm = ttk.Frame(dlg, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(1, weight=1)

        summary = (
            f"{preview.unique_outputs} unique output file(s) · "
            f"{preview.selected} selected · "
            f"{preview.resolved} resolved · "
            f"{preview.duplicates} duplicate(s) · "
            f"{preview.missing} missing"
        )
        ttk.Label(frm, text=summary, wraplength=930).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        table_frame = ttk.Frame(frm)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("action", "quality", "size")
        table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
            height=18,
        )
        table.heading("#0", text="Input file", anchor="w")
        table.heading("action", text="Action", anchor="w")
        table.heading("quality", text="Quality", anchor="w")
        table.heading("size", text="Size", anchor="e")
        table.column("#0", width=400, stretch=True, minwidth=160)
        table.column("action", width=110, stretch=False, anchor="w")
        table.column("quality", width=150, stretch=False, anchor="w")
        table.column("size", width=130, stretch=False, minwidth=120, anchor="e")
        yscroll = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=table.yview
        )
        table.configure(yscrollcommand=yscroll.set)
        table.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        for item in preview.items:
            action = PREVIEW_ACTION_LABELS.get(item.action, item.action)
            depth = BIT_DEPTH_LABELS.get(str(item.bit_depth), f"{item.bit_depth}-bit")
            rate = SAMPLE_RATE_LABELS.get(
                str(item.sample_rate), f"{item.sample_rate} Hz"
            )
            quality = f"{depth} / {rate}"
            table.insert(
                "",
                tk.END,
                text=item.source_display,
                values=(action, quality, item.size_display),
            )

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        btns.columnconfigure(0, weight=1)

        def on_back() -> None:
            self._discard_prepared_conversion()

        def on_convert() -> None:
            self._confirm_prepared_conversion()

        ttk.Button(
            btns, text="Back", command=on_back, width=ACTION_BUTTON_WIDTH
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            btns, text="Convert", command=on_convert, width=ACTION_BUTTON_WIDTH
        ).grid(row=0, column=1, sticky="e")
        dlg.protocol("WM_DELETE_WINDOW", on_back)
        dlg.bind("<Escape>", lambda _e: on_back())
        try:
            dlg.grab_set()
        except tk.TclError:
            pass
        self._place_dialog_over_app(dlg)
        self.status_var.set("Review conversion…")
        self._animate_progress_to(0, snap=True)

    def _close_preview_dialog(self) -> None:
        dlg = self._preview_dialog
        self._preview_dialog = None
        if dlg is None:
            return
        try:
            dlg.grab_release()
        except tk.TclError:
            pass
        try:
            dlg.destroy()
        except tk.TclError:
            pass

    def _discard_prepared_conversion(self) -> None:
        self._close_preview_dialog()
        self._prepared_conversion = None
        self._write_prepared = None
        self._cancel_event.clear()
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self._set_idle_status(self._tracklist_selection_summary())

    def _confirm_prepared_conversion(self) -> None:
        prepared = self._prepared_conversion
        if prepared is None:
            return
        self._close_preview_dialog()
        self._prepared_conversion = None
        self._write_prepared = prepared
        self.status_var.set("Converting…")
        threading.Thread(target=self._write_worker, daemon=True).start()

    def _write_worker(self) -> None:
        prepared = self._write_prepared
        if prepared is None:
            self._ui(lambda: self._finish_error("Nothing to convert."))
            return
        plans = prepared.plans
        items = prepared.items
        manifest = prepared.manifest
        wav_dir = prepared.wav_dir
        output = prepared.output
        skipped = list(prepared.skipped)
        try:
            summaries: list[str] = []
            total = len(items)

            def on_progress(
                current: int,
                _plan_total: int,
                action: str,
                track_name: str,
            ) -> None:
                self._ui(
                    lambda o=current, t=total, a=action, n=track_name: self._set_progress(
                        o, t, action=a, name=n
                    )
                )

            def _finish_cancel_with_errors(
                encode_errors: list[str] | None = None,
            ) -> None:
                self._ui(lambda e=encode_errors: self._finish_cancelled(e or None))

            if self._cancel_event.is_set():
                _finish_cancel_with_errors()
                return
            try:
                converter_manifest.save_manifest(manifest, wav_dir)
            except OSError as exc:
                self._ui(
                    lambda e=[f"cannot write converter manifest: {exc}"]: self._finish_error(
                        e
                    )
                )
                return
            batch_stats = rb.convert_unique(
                plans[0],
                force=False,
                progress=False,
                on_progress=on_progress,
                cancel_event=self._cancel_event,
                items=items,
            )
            completed_plans: list[rb.Plan] = []
            cancelled = self._cancel_event.is_set()
            for plan in plans:
                if cancelled or self._cancel_event.is_set():
                    cancelled = True
                    break
                appended = rb.apply_xml(plan, batch_stats.succeeded)
                completed_plans.append(plan)
                if self._cancel_event.is_set():
                    cancelled = True
                parts = []
                if batch_stats.converted and plan is plans[0]:
                    parts.append(f"{batch_stats.converted} converted")
                if batch_stats.copied and plan is plans[0]:
                    parts.append(f"{batch_stats.copied} copied")
                if batch_stats.skipped and plan is plans[0]:
                    parts.append(f"{batch_stats.skipped} skipped")
                if appended:
                    parts.append(f"+{appended} playlist entries")
                if plan.warnings:
                    parts.append(f"{len(plan.warnings)} missing skipped")
                detail = ", ".join(parts) if parts else "done"
                summaries.append(f"{plan.wav_playlist_name}: {detail}")

            if completed_plans:
                try:
                    rb.write_import_xml(
                        completed_plans[0].output_root, completed_plans[0].output
                    )
                except rb.CliError as exc:
                    self._ui(lambda e=[str(exc)]: self._finish_error(e))
                    return
                if self._cancel_event.is_set():
                    cancelled = True

            if cancelled:
                _finish_cancel_with_errors(batch_stats.errors or None)
                return
            if total == 0:
                self._ui(lambda: self._set_progress(0, 0))
            else:
                self._ui(lambda t=total: self._set_progress(t, t))
            if batch_stats.errors:
                self._ui(lambda e=batch_stats.errors: self._finish_error(e))
                return
            open_dir = plans[0].playlist_dir
            out = str(output)
            if total_successful_conversions([batch_stats]) == 0:
                self._ui(
                    lambda s=summaries, w=skipped: self._finish_no_conversions(s, w)
                )
            else:
                self._ui(
                    lambda s=summaries, o=out, w=skipped, d=open_dir, x=output: self._finish_ok(
                        s, o, w, d, x
                    )
                )
        except rb.CliError as exc:
            self._ui(lambda e=str(exc): self._finish_error(e))
        except Exception as exc:  # noqa: BLE001 — show unexpected errors in UI
            self._ui(lambda e=str(exc): self._finish_error(e))
        finally:
            self._write_prepared = None

    def _show_conversion_errors(self, message: str | list[str]) -> None:
        lines = message if isinstance(message, list) else message.splitlines()
        self._show_list_dialog(
            "Conversion failed",
            "These errors occurred during conversion:",
            lines,
            wait=False,
        )

    def _finish_cancelled(self, errors: str | list[str] | None = None) -> None:
        self._close_preview_dialog()
        self._prepared_conversion = None
        self._write_prepared = None
        self._set_busy(False)
        self.status_var.set("Cancelled.")
        self._cancel_cancelled_clear()
        self._cancelled_clear_id = self.root.after(
            CANCELLED_STATUS_CLEAR_MS, self._clear_cancelled_status
        )
        if errors:
            self._show_conversion_errors(errors)

    def _finish_error(self, message: str | list[str]) -> None:
        self._close_preview_dialog()
        self._prepared_conversion = None
        self._write_prepared = None
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self.status_var.set("Failed.")
        self._show_conversion_errors(message)

    def _finish_no_conversions(
        self,
        summaries: list[str],
        warnings: list[str] | None = None,
    ) -> None:
        self._prepared_conversion = None
        self._write_prepared = None
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self.status_var.set("Finished with no audio files converted or copied.")
        if warnings:
            self._show_list_dialog(
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
        import_xml: Path | None = None,
    ) -> None:
        self._prepared_conversion = None
        self._write_prepared = None
        self._set_busy(False)
        self._animate_progress_to(100, snap=True)
        body = "\n".join(summaries)
        self.status_var.set(
            f"Done. Point Rekordbox Imported Library at:\n{output}"
        )
        if warnings:
            self._show_list_dialog(
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
        self._show_done_dialog(message, open_dir, import_xml)

    def _show_list_dialog(
        self,
        title: str,
        intro: str,
        lines: list[str],
        *,
        summary: str | None = None,
        wait: bool = True,
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
            list_frame, height=min(12, max(4, len(lines))), width=72
        )
        scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=listbox.yview
        )
        listbox.configure(yscrollcommand=scroll.set)
        listbox.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for line in lines:
            listbox.insert(tk.END, line)

        btns = ttk.Frame(frm)
        btns.grid(row=row + 1, column=0, sticky="e", pady=(12, 0))

        def close() -> None:
            dlg.destroy()

        ttk.Button(btns, text="OK", command=close).pack(side=tk.RIGHT)
        dlg.bind("<Return>", lambda _e: close())
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        self._place_dialog_over_app(dlg)
        if wait:
            dlg.wait_window()

    def _show_done_dialog(
        self,
        message: str,
        open_dir: Path | None,
        import_xml: Path | None = None,
    ) -> None:
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

        def reveal_library() -> None:
            if open_dir is not None:
                open_in_finder(open_dir)
            close()

        def reveal_import_xml() -> None:
            if import_xml is not None:
                open_in_finder(import_xml)
            close()

        def open_guide() -> None:
            close()
            self._show_usage_guide()

        if open_dir is not None:
            ttk.Button(
                btns, text="Reveal audio folder", command=reveal_library
            ).pack(side=tk.LEFT, padx=(0, 8))
        if import_xml is not None:
            ttk.Button(
                btns, text="Reveal import XML", command=reveal_import_xml
            ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="Open usage guide", command=open_guide).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btns, text="OK", command=close).pack(side=tk.LEFT)
        dlg.bind("<Return>", lambda _e: close())
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        self._place_dialog_over_app(dlg)
        dlg.wait_window()

    def _start_update_check(self, *, manual: bool) -> None:
        if self._update_check_running:
            return
        self._update_check_running = True

        def worker() -> None:
            result = check_for_update(__version__)

            def on_ui(r=result, m=manual) -> None:
                self._update_check_running = False
                self._handle_update_check_result(r, manual=m)

            self._ui(on_ui)

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
                    f"Simple Rekordbox Converter {__version__} is up to date.",
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
        self._place_dialog_over_app(dlg)
        dlg.wait_window()


def main() -> int:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
