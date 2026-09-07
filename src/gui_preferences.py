"""Persistent GUI output-path preferences for the Rekordbox WAV converter."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

BUNDLE_ID = "io.github.slnnzmtl.rekordboxWavConverter"
PREFERENCES_VERSION = 1


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
    for key in ("wav_dir", "import_xml"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def save_preferences(
    wav_dir: Path,
    import_xml: Path,
    *,
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
) -> tuple[Path, Path]:
    saved_wav = saved.get("wav_dir")
    if not saved_wav:
        return default_wav_dir, default_import_xml

    candidate_wav = Path(saved_wav).expanduser()
    if not _wav_dir_is_valid(candidate_wav):
        return default_wav_dir, default_import_xml

    wav_dir = candidate_wav.resolve()

    saved_xml = saved.get("import_xml")
    if saved_xml:
        candidate_xml = Path(saved_xml).expanduser()
        if _import_xml_is_valid(candidate_xml):
            return wav_dir, candidate_xml.resolve()
    return wav_dir, wav_dir / "rekordbox-wav-import.xml"
