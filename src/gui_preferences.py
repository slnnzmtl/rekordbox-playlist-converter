"""Persistent GUI output-path preferences for Simple Rekordbox Converter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rekordbox_xml import path_is_under_documents

BUNDLE_ID = "io.github.slnnzmtl.rekordboxWavConverter"
PREFERENCES_VERSION = 1
OUTPUT_DIR_NAME = "rekordbox-converter"
IMPORT_XML_NAME = "rekordbox-import.xml"
DOCUMENTS_PROBE_TIMEOUT_SECONDS = 5.0
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


def default_output_paths(
    *,
    documents_accessible: bool,
    home: Path | None = None,
) -> tuple[Path, Path]:
    """Return default WAV dir and import XML based on Documents access."""
    base = home if home is not None else Path.home()
    if documents_accessible:
        wav_dir = base / "Documents" / OUTPUT_DIR_NAME
    else:
        wav_dir = base / OUTPUT_DIR_NAME
    return wav_dir, wav_dir / IMPORT_XML_NAME


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


DOCUMENTS_PROBE_FLAG = "--probe-documents"
FIND_REKORDBOX_XML_FLAG = "--find-rekordbox-xml"


def _app_probe_command(flag: str, path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, flag, str(path)]
    gui = Path(__file__).resolve().parent / "rb_converter_gui.py"
    return [sys.executable, str(gui), flag, str(path)]


def documents_probe_command(path: Path) -> list[str]:
    """Argv for a same-app child that lists *path* under this process's TCC."""
    return _app_probe_command(DOCUMENTS_PROBE_FLAG, path)


def find_rekordbox_xml_command(path: Path) -> list[str]:
    """Argv for a same-app child that walks *path* for Rekordbox XML filenames."""
    return _app_probe_command(FIND_REKORDBOX_XML_FLAG, path)


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


def default_config_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / BUNDLE_ID
        / "preferences.json"
    )


def load_preferences(config_path: Path | None = None) -> dict[str, str]:
    path = config_path or default_config_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    if raw.get("version") != PREFERENCES_VERSION:
        return {}
    result: dict[str, str] = {}
    for key in ("wav_dir", "import_xml", "source_xml"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    fmt = raw.get("output_format")
    if isinstance(fmt, str) and fmt.strip().lower() in ("wav", "aiff"):
        result["output_format"] = fmt.strip().lower()
    depth = raw.get("bit_depth")
    if depth in (16, 24) or (isinstance(depth, str) and depth.strip() in ("16", "24")):
        result["bit_depth"] = str(depth).strip()
    rate = raw.get("sample_rate")
    if rate in (44100, 48000) or (
        isinstance(rate, str) and rate.strip() in ("44100", "48000")
    ):
        result["sample_rate"] = str(rate).strip()
    return result


def save_preferences(
    wav_dir: Path,
    import_xml: Path,
    *,
    source_xml: Path | None = None,
    output_format: str | None = None,
    bit_depth: int | str | None = None,
    sample_rate: int | str | None = None,
    config_path: Path | None = None,
) -> None:
    path = config_path or default_config_path()
    wav_s = str(wav_dir.expanduser().resolve())
    xml_s = str(import_xml.expanduser().resolve())
    payload: dict[str, Any] = {
        "version": PREFERENCES_VERSION,
        "wav_dir": wav_s,
        "import_xml": xml_s,
    }
    if source_xml is not None:
        payload["source_xml"] = str(source_xml.expanduser().resolve())
    else:
        existing = load_preferences(config_path=path).get("source_xml")
        if existing:
            payload["source_xml"] = existing
    if output_format is not None and output_format in ("wav", "aiff"):
        payload["output_format"] = output_format
    if bit_depth is not None:
        depth_s = str(bit_depth).strip()
        if depth_s in ("16", "24"):
            payload["bit_depth"] = depth_s
    if sample_rate is not None:
        rate_s = str(sample_rate).strip()
        if rate_s in ("44100", "48000"):
            payload["sample_rate"] = rate_s
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=".preferences-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _wav_dir_is_valid(path: Path) -> bool:
    expanded = path.expanduser()
    if expanded.is_dir():
        return True
    parent = expanded.parent
    return parent.exists() and parent.is_dir()


def _import_xml_is_valid(path: Path) -> bool:
    parent = path.expanduser().parent
    return parent.exists() and parent.is_dir()


def resolve_startup_paths(
    saved: dict[str, str],
    *,
    default_wav_dir: Path,
    default_import_xml: Path,
    documents_accessible: bool = True,
    home: Path | None = None,
) -> tuple[Path, Path]:
    saved_wav = saved.get("wav_dir")
    if not saved_wav:
        return default_wav_dir, default_import_xml

    candidate_wav = Path(saved_wav).expanduser()
    if not documents_accessible and path_is_under_documents(
        candidate_wav, home=home
    ):
        return default_wav_dir, default_import_xml
    if not _wav_dir_is_valid(candidate_wav):
        return default_wav_dir, default_import_xml

    wav_dir = candidate_wav.resolve()

    saved_xml = saved.get("import_xml")
    if saved_xml:
        candidate_xml = Path(saved_xml).expanduser()
        if not documents_accessible and path_is_under_documents(
            candidate_xml, home=home
        ):
            return wav_dir, wav_dir / IMPORT_XML_NAME
        if _import_xml_is_valid(candidate_xml):
            return wav_dir, candidate_xml.resolve()
    return wav_dir, wav_dir / IMPORT_XML_NAME
