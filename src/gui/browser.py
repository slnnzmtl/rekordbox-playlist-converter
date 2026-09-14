"""Browser pane builders for playlist tree and tracklist (explicit callbacks)."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from gui.layout import ACTION_BUTTON_WIDTH, HoverTooltip, bind_wraplength, tree_with_yscroll
from gui.tracklist import TRACKLIST_VALUE_COLUMNS


def build_menubar(
    root: tk.Tk,
    *,
    on_search_xml: Callable[[], None],
    on_usage: Callable[[], None],
    on_updates: Callable[[], None],
) -> tk.Menu:
    menubar = tk.Menu(root)
    file_menu = tk.Menu(menubar, tearoff=0)
    file_menu.add_command(
        label="Search for Rekordbox XML…",
        command=on_search_xml,
    )
    menubar.add_cascade(label="File", menu=file_menu)
    help_menu = tk.Menu(menubar, tearoff=0)
    help_menu.add_command(
        label="How to Use…",
        command=on_usage,
        accelerator="Command-?",
    )
    help_menu.add_command(
        label="Check for Updates…",
        command=on_updates,
    )
    menubar.add_cascade(label="Help", menu=help_menu)
    root.config(menu=menubar)
    return menubar


def build_playlist_pane(
    parent: ttk.Frame,
    *,
    search_var: tk.StringVar,
    bind_search: Callable[[ttk.Entry], None],
    on_select: Callable[..., None],
    on_button1: Callable[..., str | None],
) -> tuple[ttk.Frame, ttk.Entry, ttk.Treeview]:
    """Left browser: playlist search + tree. Returns (pane, search_entry, tree)."""
    left = ttk.Frame(parent)
    left.columnconfigure(0, weight=1)
    left.rowconfigure(1, weight=1)
    search_entry = ttk.Entry(left, textvariable=search_var)
    search_entry.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    bind_search(search_entry)
    playlist_tree, scroll = tree_with_yscroll(
        left, show="tree", selectmode="extended", height=12
    )
    playlist_tree.grid(row=1, column=0, sticky="nsew")
    scroll.grid(row=1, column=1, sticky="ns")
    playlist_tree.bind("<<TreeviewSelect>>", on_select, add="+")
    playlist_tree.bind("<Button-1>", on_button1, add="+")
    return left, search_entry, playlist_tree


def build_tracklist_pane(
    parent: ttk.Frame,
    *,
    search_var: tk.StringVar,
    bind_search: Callable[[ttk.Entry], None],
    on_select: Callable[..., None],
    on_button1: Callable[..., str | None],
    on_group_open: Callable[..., None],
    on_sort: Callable[[str], None],
    header_selected_tag: str,
    header_selected_bg: str,
    header_selected_fg: str,
) -> tuple[ttk.Frame, ttk.Entry, ttk.Treeview]:
    """Right browser: track search + tree. Returns (pane, search_entry, tree)."""
    right = ttk.Frame(parent)
    right.columnconfigure(0, weight=1)
    right.rowconfigure(1, weight=1)
    search_entry = ttk.Entry(right, textvariable=search_var)
    search_entry.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    bind_search(search_entry)
    tracklist_tree, track_scroll = tree_with_yscroll(
        right,
        columns=TRACKLIST_VALUE_COLUMNS,
        show="tree headings",
        selectmode="extended",
        height=12,
    )
    tracklist_tree.heading(
        "#0", text="#", anchor="center", command=lambda: on_sort("#0")
    )
    tracklist_tree.heading(
        "track",
        text="Track",
        anchor="w",
        command=lambda: on_sort("track"),
    )
    tracklist_tree.heading(
        "format",
        text="Format",
        anchor="w",
        command=lambda: on_sort("format"),
    )
    tracklist_tree.heading(
        "bit_depth",
        text="Bit depth",
        anchor="w",
        command=lambda: on_sort("bit_depth"),
    )
    tracklist_tree.heading(
        "sample_rate",
        text="Sample rate",
        anchor="w",
        command=lambda: on_sort("sample_rate"),
    )
    tracklist_tree.heading(
        "rating",
        text="Rating",
        anchor="w",
        command=lambda: on_sort("rating"),
    )
    tracklist_tree.column("#0", width=40, stretch=False, anchor="center", minwidth=40)
    tracklist_tree.column("track", stretch=True, minwidth=120)
    tracklist_tree.column("format", width=70, stretch=False, anchor="center")
    tracklist_tree.column("bit_depth", width=90, stretch=False, anchor="center")
    tracklist_tree.column("sample_rate", width=90, stretch=False, anchor="center")
    tracklist_tree.column("rating", width=90, stretch=False, anchor="w")
    tracklist_tree.tag_configure(
        header_selected_tag,
        background=header_selected_bg,
        foreground=header_selected_fg,
    )
    tracklist_tree.grid(row=1, column=0, sticky="nsew")
    track_scroll.grid(row=1, column=1, sticky="ns")
    tracklist_tree.bind("<<TreeviewSelect>>", on_select, add="+")
    tracklist_tree.bind("<Button-1>", on_button1, add="+")
    tracklist_tree.bind("<<TreeviewOpen>>", on_group_open, add="+")
    tracklist_tree.bind("<<TreeviewClose>>", on_group_open, add="+")
    return right, search_entry, tracklist_tree


def build_format_quality_controls(
    frm: ttk.Frame,
    *,
    pad: dict[str, Any],
    format_var: tk.StringVar,
    bit_depth_var: tk.StringVar,
    sample_rate_var: tk.StringVar,
    bit_depth_labels: dict[str, str],
    sample_rate_labels: dict[str, str],
    bit_depth_tooltip: str,
    sample_rate_tooltip: str,
    on_persist: Callable[[], None],
    on_bit_depth: Callable[..., None],
    on_sample_rate: Callable[..., None],
) -> tuple[ttk.Radiobutton, ttk.Radiobutton, ttk.Combobox, ttk.Combobox]:
    """Format radios + max-quality comboboxes on rows 5–6."""
    ttk.Label(frm, text="Format").grid(row=5, column=0, sticky="w", **pad)
    format_opts = ttk.Frame(frm)
    format_opts.grid(row=5, column=1, sticky="w", **pad)
    format_wav_radio = ttk.Radiobutton(
        format_opts,
        text="WAV",
        variable=format_var,
        value="wav",
        command=on_persist,
    )
    format_wav_radio.pack(side=tk.LEFT)
    format_aiff_radio = ttk.Radiobutton(
        format_opts,
        text="AIFF",
        variable=format_var,
        value="aiff",
        command=on_persist,
    )
    format_aiff_radio.pack(side=tk.LEFT, padx=(8, 0))

    ttk.Label(frm, text="Max. quality").grid(row=6, column=0, sticky="w", **pad)
    quality = ttk.Frame(frm)
    quality.grid(row=6, column=1, sticky="w", **pad)
    bit_depth_combo = ttk.Combobox(
        quality,
        values=list(bit_depth_labels.values()),
        state="readonly",
        width=8,
    )
    bit_depth_combo.set(bit_depth_labels.get(bit_depth_var.get(), "24-bit"))
    bit_depth_combo.pack(side=tk.LEFT)
    bit_depth_combo.bind("<<ComboboxSelected>>", on_bit_depth, add="+")
    HoverTooltip(bit_depth_combo, bit_depth_tooltip)
    sample_rate_combo = ttk.Combobox(
        quality,
        values=list(sample_rate_labels.values()),
        state="readonly",
        width=9,
    )
    sample_rate_combo.set(sample_rate_labels.get(sample_rate_var.get(), "48 kHz"))
    sample_rate_combo.pack(side=tk.LEFT, padx=(8, 0))
    sample_rate_combo.bind("<<ComboboxSelected>>", on_sample_rate, add="+")
    HoverTooltip(sample_rate_combo, sample_rate_tooltip)
    return format_wav_radio, format_aiff_radio, bit_depth_combo, sample_rate_combo


def build_progress_convert_row(
    frm: ttk.Frame,
    *,
    pad: dict[str, Any],
    on_convert: Callable[[], None],
    on_cancel: Callable[[], None],
) -> tuple[ttk.Progressbar, ttk.Button, ttk.Button]:
    """Progress bar + Convert/Cancel buttons on row 7."""
    progress = ttk.Progressbar(frm, mode="determinate", maximum=100)
    progress.grid(row=7, column=0, columnspan=2, sticky="ew", **pad)
    progress["value"] = 0
    convert_btn = ttk.Button(
        frm,
        text="Convert",
        width=ACTION_BUTTON_WIDTH,
        command=on_convert,
    )
    convert_btn.grid(row=7, column=2, sticky="e", **pad)
    cancel_btn = ttk.Button(
        frm,
        text="Cancel",
        width=ACTION_BUTTON_WIDTH,
        command=on_cancel,
        state=tk.DISABLED,
    )
    cancel_btn.grid(row=7, column=2, sticky="e", **pad)
    cancel_btn.grid_remove()
    return progress, convert_btn, cancel_btn


def build_status_row(
    frm: ttk.Frame,
    *,
    pad: dict[str, Any],
    status_var: tk.StringVar,
    scan_status_var: tk.StringVar,
) -> ttk.Frame:
    """Bottom status line with wraplength binding."""
    status_row = ttk.Frame(frm)
    status_row.grid(row=8, column=0, columnspan=3, sticky="ew", **pad)
    status_row.columnconfigure(0, weight=1)
    status_label = ttk.Label(status_row, textvariable=status_var)
    status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
    bind_wraplength(status_label, status_row, inset=12)
    ttk.Label(status_row, textvariable=scan_status_var).pack(
        side=tk.LEFT, padx=(12, 0)
    )
    return status_row
