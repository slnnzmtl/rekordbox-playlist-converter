"""Shared Tk helpers for GUI unit tests (not collected by unittest discover)."""

from __future__ import annotations


def tk_available() -> bool:
    try:
        import _tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def mark_output_folder_valid(app) -> None:
    after_id = getattr(app, "_wav_dir_validate_after_id", None)
    if after_id is not None:
        app.root.after_cancel(after_id)
        app._wav_dir_validate_after_id = None
    app._wav_dir_checking = False
    app._wav_dir_valid = True
    app._set_wav_dir_error("")
    app._update_convert_enabled()


def pump_ui(root, times: int = 20) -> None:
    root.update_idletasks()
    for _ in range(times):
        root.update()


def seed_track_selection(app, folder="ROOT", name="Test", key="1"):
    """Paint one selected leaf so Convert can proceed without loading a real XML."""
    import tkinter as tk

    leaf = app.tracklist_tree.insert("", tk.END, text="seed")
    app._tracklist_iids[leaf] = (folder, name, key)
    app.tracklist_tree.selection_set(leaf)
    return leaf


def confirm_conversion_preview(app) -> None:
    """Confirm the in-memory prepared payload (preview Convert)."""
    app._confirm_prepared_conversion()


def start_convert_and_confirm(app, root) -> None:
    """Prepare then confirm, matching the post-preview write path."""
    app._start_convert()
    pump_ui(root)
    confirm_conversion_preview(app)
    pump_ui(root)


def find_listbox(widget):
    import tkinter as tk

    if isinstance(widget, tk.Listbox):
        return widget
    for child in widget.winfo_children():
        found = find_listbox(child)
        if found is not None:
            return found
    return None


def click_button(widget, label: str) -> bool:
    """Invoke the first ttk.Button under *widget* whose text matches *label*."""
    from tkinter import ttk

    if isinstance(widget, ttk.Button) and widget.cget("text") == label:
        widget.invoke()
        return True
    for child in widget.winfo_children():
        if click_button(child, label):
            return True
    return False


def list_dialog_text(show_list) -> str:
    """Join the list-of-lines argument from _show_list_dialog mock calls."""
    lines: list[str] = []
    for call in show_list.call_args_list:
        if len(call.args) >= 3 and isinstance(call.args[2], list):
            lines.extend(call.args[2])
    return "\n".join(lines)


def mock_convert_plan(*, n_unique: int = 2):
    """Minimal Plan-like object for GUI convert-worker tests (batch unique path)."""
    from types import SimpleNamespace
    from pathlib import Path

    unique = [
        SimpleNamespace(
            source_path=Path(f"/tmp/track{i}.flac"),
            dest_name=f"track{i}.wav",
            dest_path=Path(f"/tmp/WAV/track{i}.wav"),
            bit_depth=24,
            sample_rate=48000,
            duration_seconds=1.0,
            noop=False,
            copy_wav=False,
        )
        for i in range(n_unique)
    ]
    return SimpleNamespace(
        warnings=[],
        tracks=list(unique),
        playlist_dir=Path("/tmp/WAV"),
        unique=unique,
        wav_playlist_name="Test [WAV]",
        wav_dir=Path("/tmp"),
        output_root=object(),
        output=Path("/tmp/out.xml"),
        output_format="wav",
        cover_cache={},
    )


def empty_conversion_preview(*, selected: int = 0):
    """Minimal preview for GUI tests that stub threading.Thread (pool-hostile)."""
    import rb_playlist_to_wav as rb

    return rb.ConversionPreview(
        selected=selected,
        resolved=selected,
        unique_outputs=selected,
        duplicates=0,
        missing=0,
        items=[],
    )
