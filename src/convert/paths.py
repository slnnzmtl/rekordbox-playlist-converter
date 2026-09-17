"""Pure path, naming, and stream-target helpers for convert planning."""

from __future__ import annotations

import math
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from cli_error import CliError
from convert.quality import (
    FORMAT_DIR_NAMES,
    coerce_bit_depth,
    coerce_sample_rate,
)
from ffmpeg_tools import bits_from_raw_sample


def abs_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def source_key(path: Path) -> str:
    """NFC-normalized resolved path string identifying an existing source file."""
    return unicodedata.normalize("NFC", str(path.expanduser().resolve()))


def collision_key(name: str) -> str:
    """NFC + casefold key for filename / relative-dest collision checks."""
    return unicodedata.normalize("NFC", name).casefold()


def _unique_collision_dirent(parent: Path, filename: str) -> Path | None:
    """Return the sole file in parent whose name matches filename's collision key."""
    if not parent.is_dir():
        return None
    key = collision_key(filename)
    matches: list[Path] = []
    for entry in parent.iterdir():
        if entry.is_file() and collision_key(entry.name) == key:
            matches.append(entry)
    if len(matches) == 1:
        return matches[0]
    return None


def _same_file_path(path: Path) -> Path | None:
    """Resolve path to a concrete file for samefile comparison."""
    dirent = _unique_collision_dirent(path.parent, path.name)
    if dirent is not None:
        return dirent
    return path if path.is_file() else None


def same_file(a: Path, b: Path) -> bool:
    """True when a and b are one file, including NFC vs NFD spellings.

    When the directory lists one dirent for this collision key, compare that
    entry so pathlib is not fooled by alias paths on some external volumes.
    """
    if collision_key(a.name) != collision_key(b.name):
        return False
    try:
        left = _same_file_path(a)
        right = _same_file_path(b)
        if left is None or right is None:
            return False
        return left.samefile(right)
    except OSError:
        return False


_RESERVED_FILENAME_CHARS = '<>:"|?*'


def _clean_path_component(value: str) -> str:
    """NFC-normalize and replace unsafe characters; empty if unusable."""
    if not value or value.isspace():
        return ""
    name = unicodedata.normalize("NFC", value)
    out: list[str] = []
    for ch in name:
        if ch in "/\\\0" or ch in _RESERVED_FILENAME_CHARS or ord(ch) < 32:
            out.append("_")
        else:
            out.append(ch)
    name = "".join(out).rstrip(" .")
    if not name or name in {".", ".."}:
        return ""
    return name


def sanitize_path_component(value: str, *, fallback: str) -> str:
    """NFC-normalize and make a single path component filesystem-safe."""
    return (
        _clean_path_component(value)
        or _clean_path_component(fallback)
        or "Unknown"
    )


def preferred_relative_dest(
    track_el: ET.Element,
    *,
    output_format: str = "wav",
    stem_fallback: str = "",
) -> str:
    """Relative FORMAT/Artist - Name.ext path under wav_dir for first assignment."""
    ext = ".aiff" if output_format == "aiff" else ".wav"
    fmt = format_dir_name(output_format)
    artist = sanitize_path_component(
        track_el.get("Artist") or "", fallback="Unknown Artist"
    )
    name = sanitize_path_component(
        track_el.get("Name") or "",
        fallback=stem_fallback or "Unknown Track",
    )
    return f"{fmt}/{artist} - {name}{ext}"


def format_dir_name(output_format: str) -> str:
    """Return WAV or AIFF directory name for the output format."""
    name = FORMAT_DIR_NAMES.get(output_format)
    if name is None:
        raise CliError(f"unsupported output format: {output_format!r}")
    return name


def format_media_dir(wav_dir: Path, output_format: str) -> Path:
    """Return wav_dir/WAV or wav_dir/AIFF for audio output."""
    return wav_dir / format_dir_name(output_format)


def resolve_existing_file(path: Path) -> Path | None:
    """Return path if it exists; otherwise match by Unicode-normalized filename."""
    if path.is_file():
        return path
    parent = path.parent
    if not parent.is_dir():
        return None
    key = collision_key(path.name)
    for entry in parent.iterdir():
        if entry.is_file() and collision_key(entry.name) == key:
            return entry
    return None


def target_from_stream(
    stream: dict,
    *,
    max_bit_depth: int = 24,
    max_sample_rate: int = 48000,
) -> tuple[int, int]:
    """Return (bit_depth, sample_rate) under the selected ceiling.

    Never raises bit depth or sample rate above the source (within the ceiling).
    """
    max_bit_depth = coerce_bit_depth(max_bit_depth)
    max_sample_rate = coerce_sample_rate(max_sample_rate)

    fmt = str(stream.get("sample_fmt") or "")
    bits = bits_from_raw_sample(stream)
    if bits is None and fmt in ("s24", "s24p", "s32", "s32p"):
        bits = 24 if "24" in fmt else 32
    if bits is None:
        name = str(stream.get("codec_name") or "")
        if "16" in name:
            bits = 16
        elif "24" in name:
            bits = 24
        else:
            bits = 16
    if bits > 24:
        bits = 24
    elif bits not in (16, 24):
        bits = 16 if bits <= 16 else 24
    if bits > max_bit_depth:
        bits = max_bit_depth

    try:
        rate = int(float(stream.get("sample_rate") or 0))
    except (TypeError, ValueError):
        rate = 0
    if rate > max_sample_rate:
        if rate % 44100 == 0 and 44100 <= max_sample_rate:
            rate = 44100
        else:
            rate = max_sample_rate
    elif rate in (44100, 48000) and rate <= max_sample_rate:
        pass
    else:
        rate = 44100 if 44100 <= max_sample_rate else max_sample_rate

    return bits, rate


def parse_duration_seconds(probe: dict) -> float | None:
    """Extract duration from ffprobe JSON; invalid/non-finite/negative → None."""
    fmt = probe.get("format") or {}
    raw = fmt.get("duration") if isinstance(fmt, dict) else None
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _format_size_mb(nbytes: int, *, approximate: bool = False) -> str:
    """Human-readable size in mebibytes (1024²), one decimal place."""
    text = f"{nbytes / (1024 * 1024):.1f} MB"
    return f"≈ {text}" if approximate else text
