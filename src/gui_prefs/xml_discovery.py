"""Rekordbox XML filename discovery under home / Documents."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from gui_prefs.paths import IMPORT_XML_NAME

FIND_REKORDBOX_XML_FLAG = "--find-rekordbox-xml"
SKIP_DIR_NAMES = frozenset(
    {
        "Library",
        "Applications",
        ".Trash",
        "Trash",
        "node_modules",
        "Caches",
        "Cache",
        "Desktop",
        "Downloads",
        "iCloud Drive",
        "Mobile Documents",
    }
)


def _is_rekordbox_xml_filename(name: str) -> bool:
    lower = name.casefold()
    if lower == IMPORT_XML_NAME.casefold():
        return False
    return "rekordbox" in lower and lower.endswith(".xml")


def _should_skip_dir(name: str) -> bool:
    if name.startswith("."):
        return True
    if name in SKIP_DIR_NAMES:
        return True
    if name.casefold().endswith(".photoslibrary"):
        return True
    return False


def _collect_rekordbox_xml(
    directory: Path, found: list[Path], *, recurse: bool
) -> None:
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if recurse and not _should_skip_dir(entry.name):
                        stack.append(entry)
                    continue
                if entry.is_file() and _is_rekordbox_xml_filename(entry.name):
                    found.append(entry.resolve())
            except OSError:
                continue


def iter_rekordbox_xml_files(root: Path) -> list[Path]:
    """Return matching XML in *root* (top-level only) and under *root*/Documents."""
    base = root.expanduser()
    found: list[Path] = []
    _collect_rekordbox_xml(base, found, recurse=False)
    _collect_rekordbox_xml(base / "Documents", found, recurse=True)
    found.sort(key=lambda p: str(p).casefold())
    return found


def _app_probe_command(flag: str, path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, flag, str(path)]
    gui = Path(__file__).resolve().parent.parent / "rb_converter_gui.py"
    return [sys.executable, str(gui), flag, str(path)]


def find_rekordbox_xml_command(path: Path) -> list[str]:
    """Argv for a same-app child that walks *path* for Rekordbox XML filenames."""
    return _app_probe_command(FIND_REKORDBOX_XML_FLAG, path)


def run_find_rekordbox_xml_cli(argv: list[str]) -> int:
    """Print matching XML paths (one per line). Exit 0 always when flag is present."""
    if FIND_REKORDBOX_XML_FLAG not in argv:
        return 2
    idx = argv.index(FIND_REKORDBOX_XML_FLAG)
    raw = argv[idx + 1] if idx + 1 < len(argv) else str(Path.home())
    for path in iter_rekordbox_xml_files(Path(raw)):
        print(path)
    return 0


def find_rekordbox_xml_via_child(
    root: Path,
    *,
    timeout_seconds: float | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[Path]:
    """Walk *root* in a same-app child; return matching XML paths."""
    runner = run if run is not None else subprocess.run
    try:
        completed = runner(
            find_rekordbox_xml_command(root),
            timeout=timeout_seconds,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    lines = (completed.stdout or "").splitlines()
    return [Path(line.strip()) for line in lines if line.strip()]
