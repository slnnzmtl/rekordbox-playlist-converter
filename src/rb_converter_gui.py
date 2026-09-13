#!/usr/bin/env python3
"""Tiny tkinter front-end for Simple Rekordbox Converter."""

from __future__ import annotations

import sys

from gui_preferences import (
    DOCUMENTS_PROBE_FLAG,
    FIND_REKORDBOX_XML_FLAG,
    run_documents_probe_cli,
    run_find_rekordbox_xml_cli,
)

# Same-app TCC child must not import tkinter (slow) or show a window.
if DOCUMENTS_PROBE_FLAG in sys.argv:
    raise SystemExit(run_documents_probe_cli(sys.argv[1:]))
if FIND_REKORDBOX_XML_FLAG in sys.argv:
    raise SystemExit(run_find_rekordbox_xml_cli(sys.argv[1:]))

import tkinter as tk

from gui.app import ConverterApp
from gui.constants import (
    DEFAULT_OUTPUT,
    DEFAULT_WAV_DIR,
    FALLBACK_OUTPUT,
    FALLBACK_WAV_DIR,
)
from gui.helpers import (
    PreparedConversion,
    app_logo_path,
    app_window_icon_path,
    progress_action_status_hint,
    total_successful_conversions,
)
from gui.runtime import open_in_finder

__all__ = [
    "ConverterApp",
    "PreparedConversion",
    "DEFAULT_WAV_DIR",
    "DEFAULT_OUTPUT",
    "FALLBACK_WAV_DIR",
    "FALLBACK_OUTPUT",
    "progress_action_status_hint",
    "total_successful_conversions",
    "app_logo_path",
    "app_window_icon_path",
    "open_in_finder",
    "main",
]


def main() -> int:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
