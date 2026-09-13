"""Documents folder access via same-app child process (macOS TCC)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

DOCUMENTS_PROBE_TIMEOUT_SECONDS = 5.0
DOCUMENTS_PROBE_FLAG = "--probe-documents"


def probe_folder_access(
    probe: Callable[[], bool],
    *,
    timeout_seconds: float = DOCUMENTS_PROBE_TIMEOUT_SECONDS,
) -> bool:
    """Run *probe* in a worker thread; return False on timeout, error, or False."""
    result: list[bool] = []

    def runner() -> None:
        try:
            result.append(bool(probe()))
        except Exception:
            result.append(False)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive() or not result:
        return False
    return result[0]


def documents_folder_probe(path: Path | None = None) -> Callable[[], bool]:
    """Return a probe that checks whether *path* (default ~/Documents) is a dir."""
    target = path if path is not None else Path.home() / "Documents"

    def _probe() -> bool:
        return target.is_dir()

    return _probe


def _app_probe_command(flag: str, path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, flag, str(path)]
    gui = Path(__file__).resolve().parent.parent / "rb_converter_gui.py"
    return [sys.executable, str(gui), flag, str(path)]


def documents_probe_command(path: Path) -> list[str]:
    """Argv for a same-app child that lists *path* under this process's TCC."""
    return _app_probe_command(DOCUMENTS_PROBE_FLAG, path)


def run_documents_probe_cli(argv: list[str]) -> int:
    """Exit code for --probe-documents [path]: 0 if the folder can be listed.

    Listing (not merely is_dir) is what triggers macOS Documents TCC.
    """
    if DOCUMENTS_PROBE_FLAG not in argv:
        return 2
    idx = argv.index(DOCUMENTS_PROBE_FLAG)
    raw = argv[idx + 1] if idx + 1 < len(argv) else str(Path.home() / "Documents")
    target = Path(raw).expanduser()
    try:
        os.listdir(target)
    except OSError:
        return 1
    return 0


def probe_path_via_child(
    path: Path,
    *,
    timeout_seconds: float | None = None,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    """List *path* in a same-app child so a TCC hang cannot freeze this process.

    Uses this app (or this Python + GUI script), not /bin/test — system test
    ignores Files and Folders and would treat Documents as allowed after dismiss.

    Default timeout is None: wait until the user answers the system prompt.
    A short timeout kills the helper and the dialog never stays on screen.
    Never call is_dir()/listdir() in this process.
    """
    runner = run if run is not None else subprocess.run
    try:
        completed = runner(
            documents_probe_command(path),
            timeout=timeout_seconds,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0
