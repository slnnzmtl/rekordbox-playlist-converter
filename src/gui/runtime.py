"""Patched GUI namespace: tests set gui_tk.GUI_MODULE to this module.

Mixin / app code must look up patched names here (runtime.threading,
runtime.prepare, runtime.show_centered_message, …). Local imports of
those names make patches miss.
"""

from __future__ import annotations

import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog

import converter_manifest
from cli_error import CancelledError, CliError
from convert import prepare, prepare_batch
from convert.format_policy import SUPPORTED_LOSSLESS_EXT
from convert.preview import insufficient_output_space_message, preview_block_message, preview_dialog_footer, preview_write_bytes
from convert.write import execute_prepared
from gui.dialogs import ask_centered_yesno, show_centered_message
from gui_prefs import (
    find_rekordbox_xml_via_child,
    import_xml_path,
    load_preferences,
    probe_path_via_child,
    resolve_startup_paths,
    save_preferences,
)
from analytics import (
    disable_analytics,
    enable_analytics,
    flush_pending,
    report_conversion,
)
from import_edit import load_import_edit_draft, save_import_edit_draft
from rekordbox_xml import (
    collection_indexes,
    decode_location,
    duplicate_playlist_name_error,
    iter_playlist_nodes,
    load_dj_playlists,
    path_is_under_documents,
    playlist_label,
    playlist_preview_track_count,
    track_included_in_playlist_preview,
    UNKNOWN_PLAYLIST_NAME,
    unreferenced_collection_track_ids,
    unknown_playlist_node,
)
from update_check import check_for_update


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


__all__ = [
    "CancelledError",
    "CliError",
    "SUPPORTED_LOSSLESS_EXT",
    "threading",
    "time",
    "webbrowser",
    "filedialog",
    "show_centered_message",
    "ask_centered_yesno",
    "prepare",
    "prepare_batch",
    "converter_manifest",
    "execute_prepared",
    "load_preferences",
    "save_preferences",
    "enable_analytics",
    "disable_analytics",
    "flush_pending",
    "report_conversion",
    "resolve_startup_paths",
    "find_rekordbox_xml_via_child",
    "probe_path_via_child",
    "import_xml_path",
    "load_import_edit_draft",
    "save_import_edit_draft",
    "check_for_update",
    "open_in_finder",
    "collection_indexes",
    "decode_location",
    "duplicate_playlist_name_error",
    "iter_playlist_nodes",
    "load_dj_playlists",
    "path_is_under_documents",
    "playlist_label",
    "playlist_preview_track_count",
    "track_included_in_playlist_preview",
    "UNKNOWN_PLAYLIST_NAME",
    "unreferenced_collection_track_ids",
    "unknown_playlist_node",
    "insufficient_output_space_message",
    "preview_block_message",
    "preview_dialog_footer",
    "preview_write_bytes",
]
