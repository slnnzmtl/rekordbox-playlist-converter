"""Dialog widget builders for Simple Rekordbox Converter.

Callers keep thin ConverterApp._show_* methods so patch.object seams stay valid.
Pass open_in_finder / other namespace-bound callables in from rb_converter_gui.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import scrolledtext, ttk
from typing import Any

from gui.layout import (
    ACTION_BUTTON_WIDTH,
    bind_wraplength,
    listbox_with_yscroll,
    make_dialog,
    place_dialog_over_parent,
    tree_with_yscroll,
)


def show_xml_choice_dialog(
    parent: tk.Tk,
    paths: list[Path],
    *,
    on_open: Callable[[Path], None],
) -> None:
    """Modal list of Rekordbox XML paths; calls *on_open* with the chosen path."""
    dlg, frm = make_dialog(
        parent, "Choose Rekordbox XML", grab=True, resizable=True
    )
    frm.rowconfigure(1, weight=1)

    intro = ttk.Label(
        frm,
        text="Several Rekordbox XML files were found. Choose one to load:",
    )
    intro.grid(row=0, column=0, sticky="w", pady=(0, 8))
    bind_wraplength(intro, frm, inset=32)

    list_frame = ttk.Frame(frm)
    list_frame.grid(row=1, column=0, sticky="nsew")
    list_frame.columnconfigure(0, weight=1)
    list_frame.rowconfigure(0, weight=1)
    listbox, scroll = listbox_with_yscroll(
        list_frame, height=min(8, max(3, len(paths))), width=72
    )
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
        on_open(paths[int(selection[0])])
        close()

    ttk.Button(btns, text="Cancel", command=close).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(btns, text="Open", command=open_selected).pack(side=tk.LEFT)
    listbox.bind("<Double-Button-1>", lambda _e: open_selected())
    dlg.bind("<Return>", lambda _e: open_selected())
    dlg.bind("<Escape>", lambda _e: close())
    dlg.protocol("WM_DELETE_WINDOW", close)
    place_dialog_over_parent(dlg, parent)
    dlg.wait_window()


def show_usage_guide_dialog(
    parent: tk.Tk,
    guide_text: str,
    *,
    existing: tk.Toplevel | None,
    on_closed: Callable[[], None],
) -> tk.Toplevel:
    """Non-modal usage guide; returns the dialog window."""
    if existing is not None and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return existing

    dlg, frm = make_dialog(
        parent, "How to use", resizable=True, padding=12, grab=False
    )
    dlg.geometry("640x520")
    dlg.minsize(480, 360)
    frm.rowconfigure(0, weight=1)

    text = scrolledtext.ScrolledText(
        frm, wrap=tk.WORD, width=72, height=28, font=("Menlo", 11)
    )
    text.grid(row=0, column=0, sticky="nsew")
    text.insert("1.0", guide_text.strip() + "\n")
    text.configure(state=tk.DISABLED)

    def close() -> None:
        on_closed()
        dlg.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=1, column=0, sticky="e", pady=(12, 0))
    ttk.Button(btns, text="Close", command=close).pack(side=tk.RIGHT)
    dlg.bind("<Escape>", lambda _e: close())
    dlg.protocol("WM_DELETE_WINDOW", close)
    place_dialog_over_parent(dlg, parent)
    dlg.focus_force()
    return dlg


def show_conversion_preview_dialog(
    parent: tk.Tk,
    *,
    summary: str,
    items: list[Any],
    action_labels: dict[str, str],
    bit_depth_labels: dict[str, str],
    sample_rate_labels: dict[str, str],
    space_issue: str | None,
    on_back: Callable[[], None],
    on_convert: Callable[[], None],
    place_over: Callable[[tk.Toplevel], None],
) -> tk.Toplevel:
    """Conversion preview table; caller owns Back/Convert semantics.

    Built without make_dialog so there is no transient/grab (matches prior UX).
    """
    dlg = tk.Toplevel(parent)
    dlg.title("Conversion preview")
    dlg.geometry("960x540")
    dlg.minsize(960, 540)
    dlg.resizable(True, True)

    frm = ttk.Frame(dlg, padding=16)
    frm.grid(row=0, column=0, sticky="nsew")
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(0, weight=1)
    frm.columnconfigure(0, weight=1)
    frm.rowconfigure(1, weight=1)

    summary_label = ttk.Label(frm, text=summary)
    summary_label.grid(row=0, column=0, sticky="w", pady=(0, 8))
    bind_wraplength(summary_label, frm, inset=32)

    table_frame = ttk.Frame(frm)
    table_frame.grid(row=1, column=0, sticky="nsew")
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)

    columns = ("action", "quality", "size")
    table, yscroll = tree_with_yscroll(
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
    table.grid(row=0, column=0, sticky="nsew")
    yscroll.grid(row=0, column=1, sticky="ns")

    for item in items:
        action = action_labels.get(item.action, item.action)
        depth = bit_depth_labels.get(str(item.bit_depth), f"{item.bit_depth}-bit")
        rate = sample_rate_labels.get(
            str(item.sample_rate), f"{item.sample_rate} Hz"
        )
        quality = f"{depth} / {rate}"
        table.insert(
            "",
            tk.END,
            text=item.source_display,
            values=(action, quality, item.size_display),
        )

    btn_row = 2
    if space_issue:
        issue_label = ttk.Label(frm, text=space_issue, foreground="#a40000")
        issue_label.grid(row=2, column=0, sticky="w", pady=(8, 0))
        bind_wraplength(issue_label, frm, inset=32)
        btn_row = 3

    btns = ttk.Frame(frm)
    btns.grid(row=btn_row, column=0, sticky="ew", pady=(12, 0))
    btns.columnconfigure(0, weight=1)

    ttk.Button(
        btns, text="Back", command=on_back, width=ACTION_BUTTON_WIDTH
    ).grid(row=0, column=0, sticky="w")
    convert_btn = ttk.Button(
        btns, text="Convert", command=on_convert, width=ACTION_BUTTON_WIDTH
    )
    convert_btn.grid(row=0, column=1, sticky="e")
    if space_issue:
        convert_btn.configure(state=tk.DISABLED)
    dlg.protocol("WM_DELETE_WINDOW", on_back)
    dlg.bind("<Escape>", lambda _e: on_back())
    place_over(dlg)
    return dlg


def show_list_dialog(
    parent: tk.Tk,
    title: str,
    intro: str,
    lines: list[str],
    *,
    summary: str | None = None,
    wait: bool = True,
    place_over: Callable[[tk.Toplevel], None],
) -> None:
    """Scrollable list dialog (errors / missing tracks)."""
    dlg, frm = make_dialog(parent, title, grab=True, resizable=True)
    frm.rowconfigure(2 if summary else 1, weight=1)

    row = 0
    if summary:
        summary_label = ttk.Label(frm, text=summary, justify=tk.LEFT)
        summary_label.grid(row=row, column=0, sticky="w", pady=(0, 8))
        bind_wraplength(summary_label, frm, inset=32)
        row += 1

    intro_label = ttk.Label(frm, text=intro)
    intro_label.grid(row=row, column=0, sticky="w", pady=(0, 8))
    bind_wraplength(intro_label, frm, inset=32)
    row += 1

    list_frame = ttk.Frame(frm)
    list_frame.grid(row=row, column=0, sticky="nsew")
    list_frame.columnconfigure(0, weight=1)
    list_frame.rowconfigure(0, weight=1)
    listbox, scroll = listbox_with_yscroll(
        list_frame, height=min(12, max(4, len(lines))), width=72
    )
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
    place_over(dlg)
    if wait:
        dlg.wait_window()


def show_done_dialog(
    parent: tk.Tk,
    message: str,
    *,
    open_dir: Path | None,
    import_xml: Path | None,
    reveal: Callable[[Path], None],
    on_open_guide: Callable[[], None],
    place_over: Callable[[tk.Toplevel], None],
) -> None:
    """Success dialog with optional Reveal / usage-guide actions."""
    dlg, frm = make_dialog(parent, "Done", grab=True, resizable=False)
    msg_label = ttk.Label(frm, text=message, justify=tk.LEFT)
    msg_label.grid(row=0, column=0, columnspan=2, sticky="w")
    bind_wraplength(msg_label, frm, inset=32)
    btns = ttk.Frame(frm)
    btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(16, 0))

    def close() -> None:
        dlg.destroy()

    def reveal_library() -> None:
        if open_dir is not None:
            reveal(open_dir)
        close()

    def reveal_import_xml() -> None:
        if import_xml is not None:
            reveal(import_xml)
        close()

    def open_guide() -> None:
        close()
        on_open_guide()

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
    place_over(dlg)
    dlg.wait_window()


def show_update_available_dialog(
    parent: tk.Tk,
    *,
    current_version: str,
    latest_version: str,
    release_notes: str,
    html_url: str,
    open_url: Callable[[str], None],
    place_over: Callable[[tk.Toplevel], None],
) -> None:
    """Update-available modal with Later / View release."""
    dlg, frm = make_dialog(
        parent, "Update available", grab=True, resizable=False
    )

    message = (
        f"A new version is available.\n\n"
        f"Current version: {current_version}\n"
        f"Latest version: {latest_version}"
    )
    if release_notes:
        message += f"\n\n{release_notes}"
    msg_label = ttk.Label(frm, text=message, justify=tk.LEFT)
    msg_label.grid(row=0, column=0, columnspan=2, sticky="w")
    bind_wraplength(msg_label, frm, inset=32)

    btns = ttk.Frame(frm)
    btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(16, 0))

    def close() -> None:
        dlg.destroy()

    def view_release() -> None:
        open_url(html_url)
        close()

    ttk.Button(btns, text="Later", command=close).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(btns, text="View release", command=view_release).pack(side=tk.LEFT)
    dlg.bind("<Escape>", lambda _e: close())
    dlg.protocol("WM_DELETE_WINDOW", close)
    place_over(dlg)
    dlg.wait_window()
