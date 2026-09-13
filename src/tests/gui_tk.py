"""Shared Tk helpers for GUI unit tests (not collected by unittest discover)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import Mock, patch

GUI_MODULE = "gui.runtime"  # patched namespace for ConverterApp mixins


def _patch_target(suffix: str) -> str:
    return f"{GUI_MODULE}.{suffix}"


def patch_gui(suffix: str, *args: Any, **kwargs: Any):
    """Patch a single ``GUI_MODULE.<suffix>`` target (for nested one-offs)."""
    return patch(_patch_target(suffix), *args, **kwargs)


_PATCH_KWARGS = frozenset(
    {
        "return_value",
        "side_effect",
        "new",
        "autospec",
        "spec",
        "create",
        "new_callable",
        "name",
    }
)


def _coerce_patch_kwargs(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict) and value and set(value).issubset(_PATCH_KWARGS):
        return value
    if isinstance(value, Mock):
        return {"new": value}
    return {"return_value": value}


def startup_patches(
    *,
    wav_dir: Any = None,
    output: Any = None,
    preferences: dict[str, Any] | None = None,
    check_for_update: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the common ConverterApp startup patch dict for :func:`app_patches`."""
    from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR
    from update_check import UpdateCheckResult

    result: dict[str, Any] = {
        "check_for_update": check_for_update
        if check_for_update is not None
        else UpdateCheckResult(kind="up_to_date"),
        "load_preferences": preferences if preferences is not None else {},
        "resolve_startup_paths": (
            wav_dir if wav_dir is not None else DEFAULT_WAV_DIR,
            output if output is not None else DEFAULT_OUTPUT,
        ),
        "show_centered_message": None,
        "ask_centered_yesno": {"return_value": True},
    }
    result.update(extra)
    return result


def merge_patches(*dicts: dict[str, Any] | None) -> dict[str, Any]:
    """Merge patch override dicts (later entries win)."""
    merged: dict[str, Any] = {}
    for d in dicts:
        if d:
            merged.update(d)
    return merged


@contextmanager
def app_patches(
    overrides: dict[str, Any] | None = None, /, **kwargs: Any
) -> Iterator[dict[str, Mock]]:
    """Stack patches on ``GUI_MODULE`` targets.

    Keys are dotted suffixes after ``GUI_MODULE`` (e.g. ``"save_preferences"``,
    ``"threading.Thread"``, ``"prepare_batch"``). Values are patch kwargs dicts,
    plain return values, or ``None`` for a default :class:`Mock`.

    Use :func:`startup_patches` for the usual startup stack::

        with app_patches(**startup_patches()) as mocks:
            save_prefs = mocks["save_preferences"]

    Yields a dict of mock objects keyed by suffix.
    """
    from contextlib import ExitStack

    merged: dict[str, Any] = {}
    if overrides:
        merged.update(overrides)
    merged.update(kwargs)

    mocks: dict[str, Mock] = {}
    with ExitStack() as stack:
        for suffix, value in merged.items():
            mock = stack.enter_context(
                patch(_patch_target(suffix), **_coerce_patch_kwargs(value))
            )
            mocks[suffix] = mock
        yield mocks


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


# Idle-callback drain hops for run_inline_thread / after(0) under unit tests.
PUMP_UI_IDLE_HOPS = 20


def pump_ui(root, times: int = PUMP_UI_IDLE_HOPS) -> None:
    """Drain pending Tk idle callbacks; *times* bounds nested after(0) hops."""
    root.update_idletasks()
    for _ in range(times):
        root.update()


def run_inline_thread(target=None, args=(), kwargs=None, **_kwargs):
    """threading.Thread stand-in that runs *target* synchronously on start()."""
    thread_args = tuple(args) if args is not None else ()
    thread_kwargs = dict(kwargs) if kwargs is not None else {}

    class _T:
        def start(self) -> None:
            if target is not None:
                target(*thread_args, **thread_kwargs)

        def is_alive(self) -> bool:
            return False

        def join(self, timeout=None) -> None:
            return None

    return _T()


def seed_track_selection(app, folder="ROOT", name="Test", key="1"):
    from gui.types import TrackLeafRef
    """Paint one selected leaf so Convert can proceed without loading a real XML."""
    import tkinter as tk

    leaf = app.tracklist_tree.insert("", tk.END, text="seed")
    app._tracklist_iids[leaf] = TrackLeafRef(folder=folder, name=name, key=key)
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
            passthrough=False,
            output_format="wav",
        )
        for i in range(n_unique)
    ]
    return SimpleNamespace(
        warnings=[],
        tracks=list(unique),
        media_dir=Path("/tmp/WAV"),
        unique=unique,
        wav_playlist_name="Test [WAV]",
        library_dir=Path("/tmp"),
        output_root=object(),
        output=Path("/tmp/out.xml"),
        output_format="wav",
        cover_cache={},
    )


def empty_conversion_preview(*, selected: int = 0):
    """Minimal preview for GUI tests that stub threading.Thread (pool-hostile)."""
    from convert.models import ConversionPreview

    return ConversionPreview(
        selected=selected,
        resolved=selected,
        unique_outputs=selected,
        duplicates=0,
        missing=0,
        items=[],
    )


def mock_prepared_conversion(*, n_unique: int = 2):
    """PreparedConversion for GUI tests that stub prepare_batch."""
    from convert.models import PreparedConversion
    import converter_manifest

    plan = mock_convert_plan(n_unique=n_unique)
    preview = empty_conversion_preview(selected=n_unique)
    return PreparedConversion(
        plans=[plan],
        items=list(plan.unique),
        manifest=converter_manifest.empty_manifest(),
        preview=preview,
        library_dir=plan.library_dir,
        output=plan.output,
        skipped=[],
    )
