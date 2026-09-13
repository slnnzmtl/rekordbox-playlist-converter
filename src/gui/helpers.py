"""Shared GUI helpers used by ConverterApp mixins."""

from __future__ import annotations

import sys
from pathlib import Path

from gui import constants


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
    return Path(__file__).resolve().parent.parent.parent / "assets" / name


def app_logo_path() -> Path:
    """Return the full-resolution app logo PNG."""
    return _bundled_asset(constants.APP_LOGO_NAME)


def app_window_icon_path() -> Path:
    """Return the 256px window-icon PNG used by Tk."""
    return _bundled_asset(constants.APP_WINDOW_ICON_NAME)


def total_successful_conversions(stats_list: list) -> int:
    return sum(s.converted + s.copied for s in stats_list)


def progress_action_status_hint(
    action: str, current: int, total: int, name: str
) -> str:
    """Status line with counter after the verb: Convert (n/m) TrackName…"""
    return f"{action.capitalize()} ({current}/{total}) {name}…"
