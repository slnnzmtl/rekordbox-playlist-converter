"""Patched GUI namespace: tests set gui_tk.GUI_MODULE to this module.

Mixin / app code must look up patched names here (runtime.threading,
runtime.rb.prepare, runtime.messagebox, …). Local imports of those names
make patches miss.
"""

from __future__ import annotations

import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import converter_manifest
import rb_playlist_to_wav as rb
from convert.write import execute_prepared
from gui_preferences import (
    find_rekordbox_xml_via_child,
    import_xml_path,
    load_preferences,
    probe_path_via_child,
    resolve_startup_paths,
    save_preferences,
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
    "threading",
    "time",
    "webbrowser",
    "filedialog",
    "messagebox",
    "rb",
    "converter_manifest",
    "execute_prepared",
    "load_preferences",
    "save_preferences",
    "resolve_startup_paths",
    "find_rekordbox_xml_via_child",
    "probe_path_via_child",
    "import_xml_path",
    "check_for_update",
    "open_in_finder",
]
