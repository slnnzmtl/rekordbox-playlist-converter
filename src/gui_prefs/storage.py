"""Persistent GUI preference load/save."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from convert.quality import parse_bit_depth, parse_output_format, parse_sample_rate

BUNDLE_ID = "io.github.slnnzmtl.rekordboxWavConverter"
PREFERENCES_VERSION = 1
_INSTALL_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def default_config_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / BUNDLE_ID
        / "preferences.json"
    )


def parse_analytics(value: object) -> str | None:
    if value in ("on", "off"):
        return value
    return None


def parse_install_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _INSTALL_ID_RE.fullmatch(text):
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def _read_raw(path: Path) -> dict[str, Any]:
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
    return raw


def load_preferences(config_path: Path | None = None) -> dict[str, str]:
    path = config_path or default_config_path()
    raw = _read_raw(path)
    if not raw:
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
    analytics = parse_analytics(raw.get("analytics"))
    if analytics is not None:
        result["analytics"] = analytics
    install_id = parse_install_id(raw.get("install_id"))
    if install_id is not None:
        result["install_id"] = install_id
    return result


def save_preferences(
    library_dir: Path | None = None,
    *,
    source_xml: Path | None = None,
    output_format: str | None = None,
    bit_depth: int | str | None = None,
    sample_rate: int | str | None = None,
    analytics: str | None = None,
    install_id: str | None = None,
    config_path: Path | None = None,
) -> None:
    path = config_path or default_config_path()
    existing = load_preferences(config_path=path)
    payload: dict[str, Any] = {"version": PREFERENCES_VERSION}

    if library_dir is not None:
        payload["library_dir"] = str(library_dir.expanduser().resolve())
    elif existing.get("library_dir"):
        payload["library_dir"] = existing["library_dir"]

    if source_xml is not None:
        payload["source_xml"] = str(source_xml.expanduser().resolve())
    elif existing.get("source_xml"):
        payload["source_xml"] = existing["source_xml"]

    if output_format is not None:
        fmt = parse_output_format(output_format)
        if fmt is not None:
            payload["output_format"] = fmt
    elif existing.get("output_format"):
        payload["output_format"] = existing["output_format"]

    if bit_depth is not None:
        depth = parse_bit_depth(bit_depth)
        if depth is not None:
            payload["bit_depth"] = str(depth)
    elif existing.get("bit_depth"):
        payload["bit_depth"] = existing["bit_depth"]

    if sample_rate is not None:
        rate = parse_sample_rate(sample_rate)
        if rate is not None:
            payload["sample_rate"] = str(rate)
    elif existing.get("sample_rate"):
        payload["sample_rate"] = existing["sample_rate"]

    if analytics is not None:
        parsed = parse_analytics(analytics)
        if parsed is not None:
            payload["analytics"] = parsed
    elif existing.get("analytics"):
        payload["analytics"] = existing["analytics"]

    if install_id is not None:
        parsed_id = parse_install_id(install_id)
        if parsed_id is not None:
            payload["install_id"] = parsed_id
    elif existing.get("install_id"):
        payload["install_id"] = existing["install_id"]

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
