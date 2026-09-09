#!/usr/bin/env python3
"""Convert a Rekordbox playlist's lossless tracks to WAV and write import XML."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, unquote

DEFAULT_WAV_DIR = Path("output")
DEFAULT_OUTPUT = Path("output") / "rekordbox-wav-import.xml"
WAV_SUFFIX = " [WAV]"
XML_CANDIDATE_RELATIVE = (
    Path("rekordbox.xml"),
    Path("Rekordbox-collection.xml"),
    Path.home() / "Documents" / "rekordbox" / "rekordbox.xml",
    Path.home() / "Documents" / "rekordbox" / "Playlists" / "Rekordbox-collection.xml",
)
SUPPORTED_LOSSLESS_EXT = {".flac", ".aiff", ".aif", ".wav", ".wave", ".m4a", ".caf"}
WAV_EXT = {".wav", ".wave"}
ALAC_EXT = {".m4a", ".caf"}

CODEC_BY_DEPTH = {
    16: "pcm_s16le",
    24: "pcm_s24le",
    32: "pcm_s32le",
}

CDJ_SAFE_SAMPLE_RATE = 44100
CDJ_SAFE_CHANNELS = 2
CDJ_SAFE_BIT_DEPTH = 16
WAVE_FORMAT_PCM = 1
CDJ_SAFE_CHUNK_IDS = ("fmt ", "data")


class CliError(Exception):
    """Fatal error with a user-facing message."""


@dataclass(frozen=True)
class WavInfo:
    format_tag: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    fmt_chunk_size: int
    chunk_ids: tuple[str, ...]


def parse_wav_info(path: Path) -> WavInfo:
    """Parse RIFF/WAVE chunk layout and the primary fmt fields."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read WAV: {path}: {exc}") from exc
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise CliError(f"not a RIFF/WAVE file: {path}")
    chunk_ids: list[str] = []
    format_tag = channels = sample_rate = bits_per_sample = fmt_chunk_size = 0
    found_fmt = False
    offset = 12
    while offset + 8 <= len(data):
        cid = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        if payload_end > len(data):
            raise CliError(f"truncated WAV chunk {cid!r} in {path}")
        chunk_ids.append(cid.decode("ascii", errors="replace"))
        if cid == b"fmt ":
            if size < 16:
                raise CliError(f"fmt chunk too small in {path}")
            format_tag, channels, sample_rate, _byte_rate, _align, bits_per_sample = (
                struct.unpack_from("<HHIIHH", data, payload_start)
            )
            fmt_chunk_size = size
            found_fmt = True
        offset = payload_end + (size % 2)
    if not found_fmt:
        raise CliError(f"WAV missing fmt chunk: {path}")
    return WavInfo(
        format_tag=format_tag,
        channels=channels,
        sample_rate=sample_rate,
        bits_per_sample=bits_per_sample,
        fmt_chunk_size=fmt_chunk_size,
        chunk_ids=tuple(chunk_ids),
    )


def is_cdj_safe_wav(path: Path) -> bool:
    """True if path is 16-bit / 44.1 kHz / stereo WAVE_FORMAT_PCM with fmt+data only."""
    if not path.is_file():
        return False
    try:
        info = parse_wav_info(path)
    except CliError:
        return False
    return (
        info.format_tag == WAVE_FORMAT_PCM
        and info.fmt_chunk_size == 16
        and info.channels == CDJ_SAFE_CHANNELS
        and info.sample_rate == CDJ_SAFE_SAMPLE_RATE
        and info.bits_per_sample == CDJ_SAFE_BIT_DEPTH
        and info.chunk_ids == CDJ_SAFE_CHUNK_IDS
    )


class Progress:
    """Single-line stderr bar. Callback always fires; stderr only when enabled."""

    def __init__(
        self,
        total: int,
        enabled: bool,
        on_progress: Callable[[int, int, str, str], None] | None = None,
    ) -> None:
        self.total = max(total, 0)
        self.enabled = enabled
        self.on_progress = on_progress
        self._width = 0

    def update(self, current: int, action: str, name: str) -> None:
        if self.on_progress is not None:
            self.on_progress(current, self.total, action, name)
        if not self.enabled:
            return
        total = self.total
        frac = 1.0 if total == 0 else min(current / total, 1.0)
        bar_w = 24
        filled = int(bar_w * frac) if total else bar_w
        bar = "#" * filled + "-" * (bar_w - filled)
        denom = total if total else current
        label = f"[{bar}] {current}/{denom}  {action}  {name}"
        cols = shutil.get_terminal_size((80, 24)).columns
        if cols > 8 and len(label) > cols - 1:
            label = label[: cols - 2] + "…"
        pad = max(self._width - len(label), 0)
        sys.stderr.write("\r" + label + (" " * pad))
        sys.stderr.flush()
        self._width = len(label)

    def close(self) -> None:
        if not self.enabled:
            return
        sys.stderr.write("\n")
        sys.stderr.flush()
        self.enabled = False


@dataclass
class PlannedTrack:
    source_el: ET.Element
    source_path: Path
    dest_path: Path
    dest_location: str
    dest_name: str
    codec: str | None  # None means copy WAV (or no-op)
    copy_wav: bool
    noop: bool


@dataclass
class Plan:
    playlist_name: str
    wav_playlist_name: str
    wav_dir: Path
    playlist_dir: Path
    output: Path
    tracks: list[PlannedTrack]  # playlist order, may repeat dest
    unique: list[PlannedTrack]  # one per dest path
    source_root: ET.Element
    output_root: ET.Element
    output_existed: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class ConvertStats:
    converted: int = 0
    copied: int = 0
    skipped: int = 0
    appended: int = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a Rekordbox playlist to CDJ-safe WAV (16-bit / 44.1 kHz / "
            "stereo PCM) and write import XML. "
            "Omit --xml/--playlist in a terminal for an interactive wizard."
        )
    )
    parser.add_argument(
        "--xml",
        type=Path,
        default=None,
        help="Source Rekordbox XML export (prompted if omitted)",
    )
    parser.add_argument(
        "--playlist",
        default=None,
        help="Playlist name, or 'folder / name' if the name is used more than once",
    )
    parser.add_argument(
        "--wav-dir",
        type=Path,
        default=DEFAULT_WAV_DIR,
        help="Directory for WAV files (default: ./output)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output Rekordbox XML (default: ./output/rekordbox-wav-import.xml)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reconvert even if a valid dest WAV already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the plan without converting or writing",
    )
    return parser.parse_args(argv)


def tool_path(name: str) -> str | None:
    """Resolve ffmpeg/ffprobe: bundled when frozen, else PATH."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / name
            if bundled.is_file():
                return str(bundled)
        beside = Path(sys.executable).resolve().parent / name
        if beside.is_file():
            return str(beside)
    return shutil.which(name)


def require_tools() -> list[str]:
    missing = []
    for name in ("ffmpeg", "ffprobe"):
        if tool_path(name) is None:
            missing.append(
                f"{name} not found on PATH (install with: brew install ffmpeg)"
            )
    return missing


def abs_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def load_dj_playlists(path: Path) -> ET.Element:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise CliError(f"Invalid XML: {path}: {exc}") from exc
    root = tree.getroot()
    if root.tag != "DJ_PLAYLISTS":
        raise CliError(f"{path} is not a Rekordbox DJ_PLAYLISTS collection")
    if root.find("COLLECTION") is None or root.find("PLAYLISTS") is None:
        raise CliError(f"{path} is not a valid Rekordbox DJ_PLAYLISTS collection")
    if root.find("PLAYLISTS/NODE") is None:
        raise CliError(f"{path} is not a valid Rekordbox DJ_PLAYLISTS collection")
    return root


def skeleton_from(source_root: ET.Element) -> ET.Element:
    version = source_root.get("Version", "1.0.0")
    root = ET.Element("DJ_PLAYLISTS", {"Version": version})
    product = source_root.find("PRODUCT")
    if product is not None:
        root.append(copy.deepcopy(product))
    else:
        ET.SubElement(root, "PRODUCT", {"Name": "rekordbox", "Version": "", "Company": ""})
    ET.SubElement(root, "COLLECTION", {"Entries": "0"})
    playlists = ET.SubElement(root, "PLAYLISTS")
    ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "0"})
    return root


def _walk_playlists(
    node: ET.Element, folder_parts: list[str]
) -> Iterable[tuple[str, str, ET.Element]]:
    """Yield (folder_path, name, node) for playlist nodes under a folder tree."""
    if node.tag != "NODE":
        return
    name = node.get("Name") or ""
    if node.get("Type") == "1":
        folder = " / ".join(folder_parts) if folder_parts else ""
        yield folder, name, node
        return
    if node.get("Type") == "0":
        next_parts = folder_parts if name == "ROOT" else [*folder_parts, name]
        for child in node:
            yield from _walk_playlists(child, next_parts)


def iter_playlists(root: ET.Element) -> list[tuple[str, str, ET.Element]]:
    """All playlists as (folder_path, name, node), depth-first."""
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        return []
    found: list[tuple[str, str, ET.Element]] = []
    for child in playlists:
        found.extend(_walk_playlists(child, []))
    return found


def find_playlists_by_name(root: ET.Element, name: str) -> list[ET.Element]:
    return [node for _folder, pl_name, node in iter_playlists(root) if pl_name == name]


def playlist_label(folder: str, name: str) -> str:
    return f"{folder} / {name}" if folder else name


def resolve_playlist(
    root: ET.Element,
    playlist_name: str,
    folder: str | None = None,
) -> tuple[tuple[str, str, ET.Element] | None, list[str]]:
    """
    Find one playlist by leaf name, folder+name, or 'folder / name' path.
    Ambiguous leaf names are an error unless folder (or a full path) is given.
    """
    entries = iter_playlists(root)
    if folder is not None:
        matches = [
            entry for entry in entries if entry[0] == folder and entry[1] == playlist_name
        ]
        label = playlist_label(folder, playlist_name)
        if not matches:
            return None, [f"playlist not found: {label}"]
        if len(matches) > 1:
            return None, [f"duplicate playlist name: {label}"]
        return matches[0], []

    by_name = [entry for entry in entries if entry[1] == playlist_name]
    if len(by_name) == 1:
        return by_name[0], []
    if len(by_name) > 1:
        listed = "\n".join(f"  {playlist_label(f, n)}" for f, n, _ in by_name)
        return None, [f"duplicate playlist name: {playlist_name}\n{listed}"]

    by_path = [entry for entry in entries if playlist_label(entry[0], entry[1]) == playlist_name]
    if len(by_path) == 1:
        return by_path[0], []
    if len(by_path) > 1:
        return None, [f"duplicate playlist name: {playlist_name}"]
    return None, [f"playlist not found: {playlist_name}"]


def playlist_track_count(node: ET.Element) -> int:
    return len(node.findall("TRACK"))


def path_is_under_documents(path: Path, *, home: Path | None = None) -> bool:
    """True if *path* is under ~/Documents without stating the filesystem."""
    base = home if home is not None else Path.home()
    documents = (base / "Documents").expanduser()
    expanded = path.expanduser()
    try:
        expanded.relative_to(documents)
        return True
    except ValueError:
        return False


def discover_xml_candidates(
    cwd: Path | None = None,
    candidates: tuple[Path, ...] | None = None,
    *,
    documents_accessible: bool = True,
    home: Path | None = None,
) -> list[Path]:
    """Existing XML paths from the default probe list (deduped, absolute).

    When *documents_accessible* is False, candidates under Documents are skipped
    without stating them (avoids hanging on macOS TCC dismiss).
    """
    base = cwd if cwd is not None else Path.cwd()
    probe = candidates if candidates is not None else XML_CANDIDATE_RELATIVE
    found: list[Path] = []
    seen: set[Path] = set()
    for rel in probe:
        path = rel if rel.is_absolute() else (base / rel)
        if not documents_accessible and path_is_under_documents(path, home=home):
            continue
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if not resolved.is_file() or resolved in seen:
            continue
        seen.add(resolved)
        found.append(resolved)
    return found


def parse_playlist_selection(
    text: str,
    entries: list[tuple[str, str, ET.Element]],
) -> tuple[list[tuple[str, str, ET.Element]], list[str]]:
    """
    Parse '1', '1,4,7', or 'all' into playlist entries.
    Rejects selecting two playlists that share the same Name.
    """
    errors: list[str] = []
    raw = text.strip().lower()
    if not raw:
        return [], ["empty selection"]
    if raw == "all":
        indices = list(range(len(entries)))
    else:
        indices = []
        for part in text.replace(" ", "").split(","):
            if not part:
                continue
            if not part.isdigit():
                errors.append(f"invalid selection: {part!r}")
                continue
            n = int(part)
            if n < 1 or n > len(entries):
                errors.append(f"selection out of range: {n}")
                continue
            indices.append(n - 1)
        if not indices and not errors:
            errors.append("empty selection")
    if errors:
        return [], errors
    chosen = [entries[i] for i in indices]
    # Deduplicate by index order while keeping first occurrence
    seen_idx: set[int] = set()
    unique_chosen: list[tuple[str, str, ET.Element]] = []
    for i, entry in zip(indices, chosen):
        if i in seen_idx:
            continue
        seen_idx.add(i)
        unique_chosen.append(entry)
    names = [name for _f, name, _n in unique_chosen]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        listed = ", ".join(sorted(dupes))
        return [], [
            f"cannot select multiple playlists with the same name: {listed}"
        ]
    return unique_chosen, []


def prompt_line(message: str, default: str | None = None) -> str:
    if default is not None:
        shown = f"{message} [{default}]: "
    else:
        shown = f"{message}: "
    try:
        value = input(shown).strip()
    except EOFError as exc:
        raise CliError("input cancelled") from exc
    if not value and default is not None:
        return default
    return value


def prompt_xml_path(existing: Path | None) -> Path:
    if existing is not None:
        return existing.expanduser()
    candidates = discover_xml_candidates()
    print("Rekordbox XML export")
    if candidates:
        for i, path in enumerate(candidates, 1):
            print(f"  {i}) {path}")
        print("  Or type a path")
        choice = prompt_line("Select XML", "1" if len(candidates) == 1 else None)
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(candidates):
                return candidates[n - 1]
            raise CliError(f"selection out of range: {n}")
        path = Path(choice).expanduser()
    else:
        print("  No common export paths found.")
        path = Path(prompt_line("Path to Rekordbox XML")).expanduser()
    if not path.is_file():
        raise CliError(f"source XML not found: {path}")
    return path


def prompt_playlists(
    root: ET.Element, playlist_arg: str | None
) -> list[tuple[str | None, str]]:
    if playlist_arg is not None:
        return [(None, playlist_arg)]
    entries = iter_playlists(root)
    if not entries:
        raise CliError("no playlists found in XML")
    print()
    print("Playlists")
    for i, (folder, name, node) in enumerate(entries, 1):
        count = playlist_track_count(node)
        print(f"  {i}) {playlist_label(folder, name)} ({count} tracks)")
    print("  Select: 1  or  1,4,7  or  all")
    while True:
        text = prompt_line("Playlists")
        chosen, errors = parse_playlist_selection(text, entries)
        if errors:
            for err in errors:
                print(f"  {err}", file=sys.stderr)
            continue
        return [(folder, name) for folder, name, _n in chosen]


def prompt_paths(wav_dir: Path, output: Path) -> tuple[Path, Path]:
    print()
    wav_s = prompt_line("WAV directory", str(wav_dir))
    out_s = prompt_line("Output XML", str(output))
    return Path(wav_s).expanduser(), Path(out_s).expanduser()


def print_import_hints(output: Path) -> None:
    print()
    print("Import into Rekordbox")
    print("  1. Preferences → View → Layout → enable rekordbox xml")
    print("  2. Preferences → Advanced → Database → Imported Library →")
    print(f"     {output}")
    print("  3. Browser → rekordbox xml → Playlists → Import Playlist")
    print("     (or drag the [WAV] playlist into Playlists)")


def run_convert_one(
    xml_path: Path,
    playlist_name: str,
    wav_dir: Path,
    output: Path,
    *,
    force: bool,
    dry_run: bool,
    playlist_folder: str | None = None,
) -> int:
    plan, errors = prepare(
        xml_path,
        playlist_name,
        wav_dir,
        output,
        playlist_folder=playlist_folder,
    )
    if errors:
        print_errors(errors)
        return 1
    assert plan is not None
    if plan.warnings:
        print_warnings(plan.warnings)
    if dry_run:
        print_summary(plan, None, dry_run=True)
        return 0
    try:
        stats = convert_unique(
            plan, force=force, progress=sys.stderr.isatty()
        )
        stats.appended = apply_xml(plan)
        atomic_write_xml(plan.output_root, plan.output)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print_summary(plan, stats, dry_run=False)
    return 0


def prompt_wizard(
    args: argparse.Namespace,
) -> tuple[Path, list[tuple[str | None, str]], Path, Path]:
    xml_path = prompt_xml_path(args.xml)
    root = load_dj_playlists(xml_path)
    names = prompt_playlists(root, args.playlist)
    wav_dir, output = prompt_paths(args.wav_dir, args.output)
    return xml_path, names, wav_dir, output


def collection_indexes(root: ET.Element) -> tuple[dict[str, ET.Element], dict[str, ET.Element]]:
    by_id: dict[str, ET.Element] = {}
    by_location: dict[str, ET.Element] = {}
    collection = root.find("COLLECTION")
    if collection is None:
        return by_id, by_location
    for track in collection.findall("TRACK"):
        tid = track.get("TrackID")
        loc = track.get("Location")
        if tid is not None:
            by_id[tid] = track
        if loc:
            by_location[loc] = track
    return by_id, by_location


def decode_location(url: str) -> Path | None:
    if not url:
        return None
    rest: str | None = None
    if url.startswith("file://localhost"):
        rest = url[len("file://localhost") :]
    elif url.startswith("file://"):
        rest = url[len("file://") :]
    else:
        return None
    if not rest:
        return None
    path = unquote(rest)
    if not path.startswith("/"):
        # file://localhost/C:/... already has a slash before the drive.
        return None
    return Path(path)


def encode_location(path: Path) -> str:
    posix = unicodedata.normalize("NFC", path.as_posix())
    quoted = quote(posix, safe="/:")
    if quoted.startswith("/"):
        return "file://localhost" + quoted
    return "file://localhost/" + quoted


def dest_name_for(source: Path) -> str:
    return unicodedata.normalize("NFC", source.stem) + ".wav"


def playlist_dir_name(playlist_name: str) -> str:
    """Filesystem-safe single directory component from the playlist name."""
    name = unicodedata.normalize("NFC", playlist_name)
    name = name.replace("/", "_").replace("\\", "_").replace("\0", "")
    name = name.rstrip(" .")
    if not name or name in {".", ".."}:
        raise CliError(f"playlist name is not usable as a directory: {playlist_name!r}")
    return name


def collision_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


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


def run_ffprobe(path: Path) -> dict:
    exe = tool_path("ffprobe")
    if exe is None:
        raise CliError(
            "ffprobe not found on PATH (install with: brew install ffmpeg)"
        )
    cmd = [
        exe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_fmt,sample_rate,channels,bits_per_raw_sample",
        "-show_entries",
        "format=format_name",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise CliError(
            "ffprobe not found on PATH (install with: brew install ffmpeg)"
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise CliError(f"ffprobe failed for {path}: {err}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise CliError(f"ffprobe returned invalid JSON for {path}") from exc


def first_stream(probe: dict) -> dict | None:
    streams = probe.get("streams") or []
    return streams[0] if streams else None


def pcm_codec_for_stream(stream: dict) -> str:
    fmt = str(stream.get("sample_fmt") or "")
    if fmt in ("flt", "fltp"):
        return "pcm_f32le"
    raw = stream.get("bits_per_raw_sample")
    bits: int | None = None
    if raw not in (None, "", "0", "N/A"):
        try:
            bits = int(raw)
        except (TypeError, ValueError):
            bits = None
    if bits is None and fmt in ("s16", "s16p"):
        bits = 16
    if bits is None:
        raise CliError("unknown bit depth")
    codec = CODEC_BY_DEPTH.get(bits)
    if codec is None:
        raise CliError(f"unknown bit depth ({bits})")
    return codec


def bit_depth_of_codec(codec: str) -> int:
    if codec == "pcm_s16le":
        return 16
    if codec == "pcm_s24le":
        return 24
    if codec in ("pcm_s32le", "pcm_f32le"):
        return 32
    raise CliError(f"unknown codec {codec}")


def classify_source(path: Path, stream: dict) -> tuple[str, bool]:
    """Return (ffmpeg_codec or 'copy', is_copy) for CDJ-safe 16-bit output."""
    ext = path.suffix.lower()
    codec_name = str(stream.get("codec_name") or "")
    if ext not in SUPPORTED_LOSSLESS_EXT:
        raise CliError(f"unsupported format: {path}")
    if ext in ALAC_EXT:
        if codec_name != "alac":
            raise CliError(f"unsupported format: {path} (expected ALAC)")
        return "pcm_s16le", False
    if ext in WAV_EXT:
        if is_cdj_safe_wav(path):
            return "copy", True
        return "pcm_s16le", False
    if ext in {".flac", ".aiff", ".aif"}:
        return "pcm_s16le", False
    raise CliError(f"unsupported format: {path}")


def resolve_playlist_tracks(
    source_root: ET.Element, playlist: ET.Element
) -> tuple[list[ET.Element], list[str]]:
    errors: list[str] = []
    by_id, by_location = collection_indexes(source_root)
    key_type = playlist.get("KeyType", "0")
    resolved: list[ET.Element] = []
    for entry in playlist.findall("TRACK"):
        key = entry.get("Key")
        if key is None or key == "":
            errors.append("playlist entry missing Key")
            continue
        if key_type == "1":
            track = by_location.get(key)
        else:
            track = by_id.get(key)
        if track is None:
            errors.append(f"missing collection track for Key={key}")
            continue
        resolved.append(track)
    return resolved, errors


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def build_plan(
    source_root: ET.Element,
    playlist_el: ET.Element,
    playlist_name: str,
    wav_dir: Path,
    output: Path,
    output_root: ET.Element,
    output_existed: bool,
) -> tuple[Plan | None, list[str]]:
    errors: list[str] = []
    tracks_el, resolve_errors = resolve_playlist_tracks(source_root, playlist_el)
    errors.extend(resolve_errors)

    wav_dir_abs = abs_path(wav_dir)
    try:
        playlist_dir = wav_dir_abs / playlist_dir_name(playlist_name)
    except CliError as exc:
        errors.append(str(exc))
        playlist_dir = wav_dir_abs / "_"

    planned: list[PlannedTrack] = []
    warnings: list[str] = []
    for el in tracks_el:
        loc = el.get("Location", "")
        source_path = decode_location(loc)
        if source_path is None:
            errors.append(f"invalid Rekordbox file URL: {loc or '(empty)'}")
            continue
        resolved = resolve_existing_file(source_path)
        if resolved is None:
            warnings.append(f"missing source file: {source_path}")
            continue
        source_path = resolved
        dest_name = dest_name_for(source_path)
        dest_path = playlist_dir / dest_name
        dest_location = encode_location(dest_path)
        planned.append(
            PlannedTrack(
                source_el=el,
                source_path=source_path,
                dest_path=dest_path,
                dest_location=dest_location,
                dest_name=dest_name,
                codec=None,
                copy_wav=False,
                noop=False,
            )
        )

    groups: dict[str, list[PlannedTrack]] = defaultdict(list)
    for item in planned:
        key = collision_key(item.dest_name)
        groups[key].append(item)

    for key, items in groups.items():
        unique_sources: dict[str, Path] = {}
        for item in items:
            unique_sources[str(item.source_path)] = item.source_path
        if len(unique_sources) > 1:
            lines = [f"Filename collision: {items[0].dest_name}"]
            for path in unique_sources.values():
                lines.append(f"  {path}")
            errors.append("\n".join(lines))

    unique: list[PlannedTrack] = []
    unique_dest: dict[str, PlannedTrack] = {}
    for item in planned:
        dest_key = str(item.dest_path)
        if dest_key in unique_dest:
            continue
        unique_dest[dest_key] = item
        unique.append(item)

        try:
            probe = run_ffprobe(item.source_path)
        except CliError as exc:
            errors.append(str(exc))
            continue
        stream = first_stream(probe)
        if stream is None:
            errors.append(f"unsupported format: {item.source_path} (no audio stream)")
            continue
        try:
            codec, is_copy = classify_source(item.source_path, stream)
        except CliError as exc:
            errors.append(str(exc))
            continue
        item.copy_wav = is_copy
        item.codec = None if is_copy else codec
        item.noop = is_copy and same_file(item.source_path, item.dest_path)
        if (not is_copy) and same_file(item.source_path, item.dest_path):
            errors.append(
                f"refusing to convert in place (source is not CDJ-safe WAV): {item.source_path}"
            )

    wav_playlist_name = f"{playlist_name}{WAV_SUFFIX}"
    plan = Plan(
        playlist_name=playlist_name,
        wav_playlist_name=wav_playlist_name,
        wav_dir=wav_dir_abs,
        playlist_dir=playlist_dir,
        output=output,
        tracks=planned,
        unique=unique,
        source_root=source_root,
        output_root=output_root,
        output_existed=output_existed,
        warnings=warnings,
    )
    if errors:
        return plan, errors
    return plan, []


@lru_cache(maxsize=1)
def ffmpeg_supports_soxr() -> bool:
    exe = tool_path("ffmpeg")
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    text = (proc.stdout or "") + (proc.stderr or "")
    return "soxr" in text.lower()


def run_ffmpeg(source: Path, dest: Path, codec: str, force: bool) -> None:
    """Encode dest as CDJ-safe 16-bit / 44.1 kHz / stereo WAVE_FORMAT_PCM.

    ``codec`` is accepted for call-site compatibility; output is always pcm_s16le.
    """
    del codec  # CDJ-safe profile is fixed for this release (option B).
    exe = tool_path("ffmpeg")
    if exe is None:
        raise CliError(
            "ffmpeg not found on PATH (install with: brew install ffmpeg)"
        )
    cmd = [
        exe,
        "-y" if force or dest.exists() else "-n",
        "-i",
        str(source),
        "-vn",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:a",
        "+bitexact",
    ]
    if ffmpeg_supports_soxr():
        cmd.extend(["-af", "aresample=resampler=soxr"])
    cmd.extend(
        [
            "-ar",
            str(CDJ_SAFE_SAMPLE_RATE),
            "-ac",
            str(CDJ_SAFE_CHANNELS),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise CliError(
            "ffmpeg not found on PATH (install with: brew install ffmpeg)"
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise CliError(f"ffmpeg conversion failed for {source}: {err}")
    if not is_cdj_safe_wav(dest):
        raise CliError(
            f"ffmpeg produced a non-CDJ-safe WAV for {source}: {dest}"
        )


def convert_unique(
    plan: Plan,
    force: bool,
    *,
    progress: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
) -> ConvertStats:
    stats = ConvertStats()
    plan.playlist_dir.mkdir(parents=True, exist_ok=True)
    items = plan.unique
    bar = Progress(len(items), progress, on_progress=on_progress)
    try:
        for i, item in enumerate(items, 1):
            name = item.dest_name
            if item.noop:
                bar.update(i, "skip", name)
                stats.skipped += 1
                continue
            if not force and is_cdj_safe_wav(item.dest_path):
                bar.update(i, "skip", name)
                stats.skipped += 1
                continue
            if item.copy_wav:
                bar.update(i, "copy", name)
                shutil.copy2(item.source_path, item.dest_path)
                stats.copied += 1
                continue
            if not item.codec:
                raise CliError(f"no codec planned for {item.source_path}")
            bar.update(i, "convert", name)
            # Existing invalid dest must be overwritten; force also overwrites.
            run_ffmpeg(item.source_path, item.dest_path, item.codec, force=True)
            stats.converted += 1
    finally:
        bar.close()
    return stats


def next_track_id(root: ET.Element) -> int:
    by_id, _ = collection_indexes(root)
    values = []
    for tid in by_id:
        try:
            values.append(int(tid))
        except ValueError:
            continue
    return (max(values) if values else 0) + 1


def ensure_root_node(playlists: ET.Element) -> ET.Element:
    for child in playlists:
        if child.tag == "NODE" and child.get("Name") == "ROOT" and child.get("Type") == "0":
            return child
    return ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "0"})


def find_or_create_wav_playlist(output_root: ET.Element, name: str) -> tuple[ET.Element, bool]:
    existing = find_playlists_by_name(output_root, name)
    if len(existing) > 1:
        raise CliError(f"duplicate playlist name in output: {name}")
    if existing:
        return existing[0], True
    playlists = output_root.find("PLAYLISTS")
    if playlists is None:
        playlists = ET.SubElement(output_root, "PLAYLISTS")
    root_node = ensure_root_node(playlists)
    node = ET.SubElement(
        root_node,
        "NODE",
        {"Name": name, "Type": "1", "KeyType": "0", "Entries": "0"},
    )
    return node, False


def share_output_root(plans: list[Plan]) -> None:
    if len(plans) < 2:
        return
    shared = plans[0].output_root
    for plan in plans[1:]:
        plan.output_root = shared


def rewrite_counts(output_root: ET.Element, wav_node: ET.Element) -> None:
    collection = output_root.find("COLLECTION")
    if collection is not None:
        collection.set("Entries", str(len(collection.findall("TRACK"))))
    wav_node.set("Entries", str(len(wav_node.findall("TRACK"))))
    playlists = output_root.find("PLAYLISTS")
    if playlists is None:
        return
    root_node = None
    for child in playlists:
        if child.tag == "NODE" and child.get("Name") == "ROOT" and child.get("Type") == "0":
            root_node = child
            break
    if root_node is not None:
        count = sum(1 for c in root_node if c.tag == "NODE")
        root_node.set("Count", str(count))


def probe_dest_tech(path: Path) -> tuple[str, str, str]:
    size = str(path.stat().st_size)
    probe = run_ffprobe(path)
    stream = first_stream(probe) or {}
    rate = str(stream.get("sample_rate") or "")
    try:
        codec = pcm_codec_for_stream(stream)
        depth = bit_depth_of_codec(codec)
    except CliError:
        name = str(stream.get("codec_name") or "")
        depth_map = {
            "pcm_s16le": 16,
            "pcm_s24le": 24,
            "pcm_s32le": 32,
            "pcm_f32le": 32,
        }
        depth = depth_map.get(name, 16)
    try:
        channels = int(stream.get("channels") or 2)
    except (TypeError, ValueError):
        channels = 2
    try:
        sr = int(float(rate)) if rate else 0
    except ValueError:
        sr = 0
    bitrate = str(int(sr * depth * channels / 1000)) if sr else "0"
    return size, bitrate, rate or "0"


def clone_track(source_el: ET.Element, track_id: str, dest_path: Path, dest_location: str) -> ET.Element:
    clone = copy.deepcopy(source_el)
    size, bitrate, sample_rate = probe_dest_tech(dest_path)
    clone.set("TrackID", track_id)
    clone.set("Location", dest_location)
    clone.set("Kind", "WAV File")
    clone.set("Size", size)
    clone.set("BitRate", bitrate)
    clone.set("SampleRate", sample_rate)
    return clone


def playlist_keys(node: ET.Element) -> list[str]:
    return [t.get("Key", "") for t in node.findall("TRACK")]


def apply_xml(plan: Plan) -> int:
    collection = plan.output_root.find("COLLECTION")
    if collection is None:
        collection = ET.SubElement(plan.output_root, "COLLECTION", {"Entries": "0"})
    _, by_location = collection_indexes(plan.output_root)
    next_id = next_track_id(plan.output_root)
    dest_to_id: dict[str, str] = {}

    for item in plan.unique:
        existing = by_location.get(item.dest_location)
        if existing is not None:
            dest_to_id[item.dest_location] = existing.get("TrackID", "")
            continue
        tid = str(next_id)
        next_id += 1
        clone = clone_track(item.source_el, tid, item.dest_path, item.dest_location)
        collection.append(clone)
        dest_to_id[item.dest_location] = tid
        by_location[item.dest_location] = clone

    wav_node, existed = find_or_create_wav_playlist(plan.output_root, plan.wav_playlist_name)
    appended = 0
    if existed:
        present = set(playlist_keys(wav_node))
        seen_this_run: set[str] = set()
        for item in plan.tracks:
            tid = dest_to_id[item.dest_location]
            if tid in present or tid in seen_this_run:
                continue
            ET.SubElement(wav_node, "TRACK", {"Key": tid})
            present.add(tid)
            seen_this_run.add(tid)
            appended += 1
    else:
        for item in plan.tracks:
            tid = dest_to_id[item.dest_location]
            ET.SubElement(wav_node, "TRACK", {"Key": tid})
            appended += 1
    rewrite_counts(plan.output_root, wav_node)
    return appended


def atomic_write_xml(root: ET.Element, path: Path) -> None:
    path = abs_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    fd, tmp = tempfile.mkstemp(prefix=".rb_wav_", suffix=".xml", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            tree = ET.ElementTree(root)
            tree.write(handle, encoding="UTF-8", xml_declaration=True)
            handle.write(b"\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def print_errors(errors: list[str]) -> None:
    for err in errors:
        print(err, file=sys.stderr)


def print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def print_summary(
    plan: Plan,
    stats: ConvertStats | None,
    dry_run: bool,
) -> None:
    unique_n = len(plan.unique)
    print("Source playlist:")
    print(plan.playlist_name)
    print()
    if dry_run:
        print("Converted:")
        missing = f" ({len(plan.warnings)} missing skipped)" if plan.warnings else ""
        print(f"{unique_n} WAV files → {plan.playlist_dir}{missing}")
        print()
        print("Output:")
        print(plan.output)
        print()
        print("New playlist:")
        print(plan.wav_playlist_name)
        return
    assert stats is not None
    print("Converted:")
    parts = []
    if stats.converted:
        parts.append(f"{stats.converted} converted")
    if stats.copied:
        parts.append(f"{stats.copied} copied")
    if stats.skipped:
        parts.append(f"{stats.skipped} skipped")
    if plan.warnings:
        parts.append(f"{len(plan.warnings)} missing skipped")
    if not parts:
        parts.append(f"{unique_n} WAV files")
    print(f"{', '.join(parts)} → {plan.playlist_dir}")
    print()
    print("Output:")
    print(plan.output)
    print()
    print("New playlist:")
    if stats.appended:
        print(f"{plan.wav_playlist_name} (+{stats.appended} entries)")
    else:
        print(plan.wav_playlist_name)


def prepare(
    xml_path: Path,
    playlist_name: str,
    wav_dir: Path,
    output: Path,
    *,
    playlist_folder: str | None = None,
) -> tuple[Plan | None, list[str]]:
    errors: list[str] = []
    errors.extend(require_tools())
    if not xml_path.is_file():
        errors.append(f"source XML not found: {xml_path}")
        return None, errors
    try:
        source_root = load_dj_playlists(xml_path)
    except CliError as exc:
        errors.append(str(exc))
        return None, errors

    found, resolve_errors = resolve_playlist(
        source_root, playlist_name, folder=playlist_folder
    )
    if resolve_errors:
        errors.extend(resolve_errors)
        return None, errors
    assert found is not None
    _folder, resolved_name, playlist_el = found

    output_path = abs_path(output)
    output_existed = output_path.is_file()
    output_root: ET.Element | None = None
    if output_existed:
        try:
            output_root = load_dj_playlists(output_path)
        except CliError as exc:
            errors.append(str(exc))
            return None, errors
    else:
        output_root = skeleton_from(source_root)

    plan, plan_errors = build_plan(
        source_root,
        playlist_el,
        resolved_name,
        wav_dir,
        output_path,
        output_root,
        output_existed,
    )
    errors.extend(plan_errors)
    return plan, errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    need_wizard = args.xml is None or args.playlist is None
    if need_wizard:
        if not sys.stdin.isatty():
            print(
                "error: --xml and --playlist are required when not running interactively",
                file=sys.stderr,
            )
            return 2
        try:
            xml_path, playlist_refs, wav_dir, output = prompt_wizard(args)
        except CliError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        assert args.xml is not None and args.playlist is not None
        xml_path = args.xml
        playlist_refs = [(None, args.playlist)]
        wav_dir = args.wav_dir
        output = args.output

    for i, (folder, name) in enumerate(playlist_refs):
        if len(playlist_refs) > 1:
            print()
            label = playlist_label(folder, name) if folder else name
            print(f"=== {label} ({i + 1}/{len(playlist_refs)}) ===")
        rc = run_convert_one(
            xml_path,
            name,
            wav_dir,
            output,
            force=args.force,
            dry_run=args.dry_run,
            playlist_folder=folder,
        )
        if rc != 0:
            return rc
    if not args.dry_run:
        print_import_hints(abs_path(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
