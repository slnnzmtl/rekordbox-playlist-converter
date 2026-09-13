"""Persistent GUI preference load/save."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from convert.quality import parse_bit_depth, parse_output_format, parse_sample_rate

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
    library = raw.get("library_dir") or raw.get("wav_dir")
    if isinstance(library, str) and library.strip():
        result["library_dir"] = library.strip()
    for key in ("source_xml",):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    fmt = parse_output_format(raw.get("output_format"))
    if fmt is not None:
        result["output_format"] = fmt
    depth = parse_bit_depth(raw.get("bit_depth"))
    if depth is not None:
        result["bit_depth"] = str(depth)
    rate = parse_sample_rate(raw.get("sample_rate"))
    if rate is not None:
        result["sample_rate"] = str(rate)
    return result


def save_preferences(
    library_dir: Path,
    *,
    source_xml: Path | None = None,
    output_format: str | None = None,
    bit_depth: int | str | None = None,
    sample_rate: int | str | None = None,
    config_path: Path | None = None,
) -> None:
    path = config_path or default_config_path()
    library_s = str(library_dir.expanduser().resolve())
    payload: dict[str, Any] = {
        "version": PREFERENCES_VERSION,
        "library_dir": library_s,
    }
    if source_xml is not None:
        payload["source_xml"] = str(source_xml.expanduser().resolve())
    else:
        existing = load_preferences(config_path=path).get("source_xml")
        if existing:
            payload["source_xml"] = existing
    if output_format is not None:
        fmt = parse_output_format(output_format)
        if fmt is not None:
            payload["output_format"] = fmt
    if bit_depth is not None:
        depth = parse_bit_depth(bit_depth)
        if depth is not None:
            payload["bit_depth"] = str(depth)
    if sample_rate is not None:
        rate = parse_sample_rate(sample_rate)
        if rate is not None:
            payload["sample_rate"] = str(rate)
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
