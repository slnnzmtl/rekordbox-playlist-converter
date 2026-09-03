#!/usr/bin/env python3
"""Convert a Rekordbox playlist's lossless tracks to WAV and write import XML."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote

DEFAULT_WAV_DIR = Path("WAV")
DEFAULT_OUTPUT = Path("rekordbox-wav-import.xml")
WAV_SUFFIX = " [WAV]"
SUPPORTED_LOSSLESS_EXT = {".flac", ".aiff", ".aif", ".wav", ".wave", ".m4a", ".caf"}
WAV_EXT = {".wav", ".wave"}
ALAC_EXT = {".m4a", ".caf"}

CODEC_BY_DEPTH = {
    16: "pcm_s16le",
    24: "pcm_s24le",
    32: "pcm_s32le",
}
FLOAT_CODECS = {"pcm_f32le"}
PCM_PREFIX = "pcm"


class CliError(Exception):
    """Fatal error with a user-facing message."""


class Progress:
    """Single-line stderr bar. No-op when not enabled (non-TTY, tests)."""

    def __init__(self, total: int, enabled: bool) -> None:
        self.total = max(total, 0)
        self.enabled = enabled
        self._width = 0

    def update(self, current: int, action: str, name: str) -> None:
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


@dataclass
class ConvertStats:
    converted: int = 0
    copied: int = 0
    skipped: int = 0
    appended: int = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a Rekordbox playlist to WAV and write import XML."
    )
    parser.add_argument("--xml", type=Path, required=True, help="Source Rekordbox XML export")
    parser.add_argument("--playlist", required=True, help="Exact playlist name to convert")
    parser.add_argument(
        "--wav-dir",
        type=Path,
        default=DEFAULT_WAV_DIR,
        help="Directory for WAV files (default: ./WAV)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output Rekordbox XML (default: ./rekordbox-wav-import.xml)",
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


def require_tools() -> list[str]:
    missing = []
    for name in ("ffmpeg", "ffprobe"):
        if shutil.which(name) is None:
            missing.append(f"{name} not found on PATH")
    return missing


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


def iter_playlist_nodes(node: ET.Element) -> Iterable[ET.Element]:
    if node.tag == "NODE" and node.get("Type") == "1":
        yield node
    for child in node:
        if child.tag == "NODE":
            yield from iter_playlist_nodes(child)


def find_playlists_by_name(root: ET.Element, name: str) -> list[ET.Element]:
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        return []
    found: list[ET.Element] = []
    for child in playlists:
        if child.tag == "NODE":
            found.extend(n for n in iter_playlist_nodes(child) if n.get("Name") == name)
    return found


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


def run_ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe",
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
        raise CliError("ffprobe not found on PATH") from exc
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


def is_valid_pcm_wav(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        probe = run_ffprobe(path)
    except CliError:
        return False
    fmt = str((probe.get("format") or {}).get("format_name") or "")
    if "wav" not in fmt.lower():
        return False
    stream = first_stream(probe)
    if stream is None:
        return False
    codec = str(stream.get("codec_name") or "")
    return codec.startswith(PCM_PREFIX)


def classify_source(path: Path, stream: dict) -> tuple[str, bool]:
    """Return (ffmpeg_codec or 'copy', is_copy)."""
    ext = path.suffix.lower()
    codec_name = str(stream.get("codec_name") or "")
    if ext not in SUPPORTED_LOSSLESS_EXT:
        raise CliError(f"unsupported format: {path}")
    if ext in ALAC_EXT:
        if codec_name != "alac":
            raise CliError(f"unsupported format: {path} (expected ALAC)")
        return pcm_codec_for_stream(stream), False
    if ext in WAV_EXT:
        return "copy", True
    if ext in {".flac", ".aiff", ".aif"}:
        return pcm_codec_for_stream(stream), False
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

    wav_dir_abs = wav_dir.expanduser()
    if not wav_dir_abs.is_absolute():
        wav_dir_abs = Path.cwd() / wav_dir_abs
    try:
        playlist_dir = wav_dir_abs / playlist_dir_name(playlist_name)
    except CliError as exc:
        errors.append(str(exc))
        playlist_dir = wav_dir_abs / "_"

    planned: list[PlannedTrack] = []
    for el in tracks_el:
        loc = el.get("Location", "")
        source_path = decode_location(loc)
        if source_path is None:
            errors.append(f"invalid Rekordbox file URL: {loc or '(empty)'}")
            continue
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

        if not item.source_path.is_file():
            errors.append(f"missing source file: {item.source_path}")
            continue
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
            msg = str(exc)
            if msg == "unknown bit depth":
                errors.append(f"unknown bit depth: {item.source_path}")
            else:
                errors.append(msg)
            continue
        item.copy_wav = is_copy
        item.codec = None if is_copy else codec
        item.noop = is_copy and same_file(item.source_path, item.dest_path)

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
    )
    if errors:
        return plan, errors
    return plan, []


def run_ffmpeg(source: Path, dest: Path, codec: str, force: bool) -> None:
    cmd = [
        "ffmpeg",
        "-y" if force or dest.exists() else "-n",
        "-i",
        str(source),
        "-vn",
        "-c:a",
        codec,
        str(dest),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise CliError("ffmpeg not found on PATH") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise CliError(f"ffmpeg conversion failed for {source}: {err}")


def convert_unique(plan: Plan, force: bool, *, progress: bool = False) -> ConvertStats:
    stats = ConvertStats()
    plan.playlist_dir.mkdir(parents=True, exist_ok=True)
    items = plan.unique
    bar = Progress(len(items), progress)
    try:
        for i, item in enumerate(items, 1):
            name = item.dest_name
            if item.noop:
                bar.update(i, "skip", name)
                stats.skipped += 1
                continue
            if not force and is_valid_pcm_wav(item.dest_path):
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
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
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
        print(f"{unique_n} WAV files → {plan.playlist_dir}")
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

    matches = find_playlists_by_name(source_root, playlist_name)
    if not matches:
        errors.append(f"playlist not found: {playlist_name}")
        return None, errors
    if len(matches) > 1:
        errors.append(f"duplicate playlist name: {playlist_name}")
        return None, errors

    output_path = output.expanduser()
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
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
        matches[0],
        playlist_name,
        wav_dir,
        output_path,
        output_root,
        output_existed,
    )
    errors.extend(plan_errors)
    return plan, errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan, errors = prepare(args.xml, args.playlist, args.wav_dir, args.output)
    if errors:
        print_errors(errors)
        return 1
    assert plan is not None
    if args.dry_run:
        print_summary(plan, None, dry_run=True)
        return 0
    try:
        stats = convert_unique(
            plan, force=args.force, progress=sys.stderr.isatty()
        )
        stats.appended = apply_xml(plan)
        atomic_write_xml(plan.output_root, plan.output)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print_summary(plan, stats, dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
