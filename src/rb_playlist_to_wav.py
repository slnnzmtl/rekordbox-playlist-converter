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
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

from cdj_aiff import (
    AIFF_SAFE_BIT_DEPTHS,
    AIFF_SAFE_RATES,
    _AiffAudioInfo,
    _extract_id3_chunk,
    _is_canonical_aiff_output,
    _normalize_aiff_audio_chunks,
    _parse_aiff_audio,
    _read_id3_frames,
    _ssnd_pcm_bytes,
    build_id3v23_tag,
    expected_id3_text_from_track,
    extract_cover_jpeg,
    is_cdj_safe_aiff,
    write_aiff_id3,
)
from cdj_wav import (
    CDJ_SAFE_BIT_DEPTH,
    CDJ_SAFE_CHANNELS,
    CDJ_SAFE_CHUNK_IDS,
    CDJ_SAFE_SAMPLE_RATE,
    WAVE_FORMAT_PCM,
    WavInfo,
    is_cdj_safe_wav,
    parse_wav_info,
)
from cli_error import CliError
from rekordbox_xml import (
    XML_CANDIDATE_RELATIVE,
    _walk_playlists,
    collection_indexes,
    decode_location,
    discover_xml_candidates,
    encode_location,
    find_playlists_by_name,
    iter_playlists,
    load_dj_playlists,
    parse_playlist_selection,
    path_is_under_documents,
    playlist_label,
    playlist_track_count,
    resolve_playlist,
    resolve_playlist_tracks,
    skeleton_from,
)

DEFAULT_WAV_DIR = Path("output")
DEFAULT_OUTPUT = Path("output") / "rekordbox-wav-import.xml"
WAV_SUFFIX = " [WAV]"
SUPPORTED_LOSSLESS_EXT = {".flac", ".aiff", ".aif", ".wav", ".wave", ".m4a", ".caf"}
WAV_EXT = {".wav", ".wave"}
ALAC_EXT = {".m4a", ".caf"}

CODEC_BY_DEPTH = {
    16: "pcm_s16le",
    24: "pcm_s24le",
    32: "pcm_s32le",
}

AIFF_SUFFIX = " [AIFF]"
AIFF_EXT = {".aiff", ".aif"}


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
        help="Directory for audio output files (default: ./output)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output Rekordbox XML (default: ./output/rekordbox-wav-import.xml)",
    )
    parser.add_argument(
        "--format",
        choices=("wav", "aiff"),
        default="wav",
        help="Output format: wav (16-bit/44.1 kHz universal) or aiff (Pioneer 24/48 ceiling)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reconvert even if a valid dest file already exists",
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


def print_import_hints(output: Path, *, output_format: str = "wav") -> None:
    suffix = " [AIFF]" if output_format == "aiff" else " [WAV]"
    print()
    print("Import into Rekordbox")
    print("  1. Preferences → View → Layout → enable rekordbox xml")
    print("  2. Preferences → Advanced → Database → Imported Library →")
    print(f"     {output}")
    print("  3. Browser → rekordbox xml → Playlists → Import Playlist")
    print(f"     (or drag the{suffix} playlist into Playlists)")


def prompt_wizard(
    args: argparse.Namespace,
) -> tuple[Path, list[tuple[str | None, str]], Path, Path, str]:
    xml_path = prompt_xml_path(args.xml)
    root = load_dj_playlists(xml_path)
    names = prompt_playlists(root, args.playlist)
    wav_dir, output = prompt_paths(args.wav_dir, args.output)
    output_format = prompt_line("Format (wav/aiff)", getattr(args, "format", "wav"))
    output_format = output_format.strip().lower()
    while output_format not in ("wav", "aiff"):
        print("  choose wav or aiff", file=sys.stderr)
        output_format = prompt_line("Format (wav/aiff)", "wav").strip().lower()
    return xml_path, names, wav_dir, output, output_format


def run_convert_one(
    xml_path: Path,
    playlist_name: str,
    wav_dir: Path,
    output: Path,
    *,
    force: bool,
    dry_run: bool,
    playlist_folder: str | None = None,
    output_format: str = "wav",
) -> int:
    plan, errors = prepare(
        xml_path,
        playlist_name,
        wav_dir,
        output,
        playlist_folder=playlist_folder,
        output_format=output_format,
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


def dest_name_for(source: Path, *, output_format: str = "wav") -> str:
    ext = ".aiff" if output_format == "aiff" else ".wav"
    return unicodedata.normalize("NFC", source.stem) + ext


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
    if codec in ("pcm_s16le", "pcm_s16be"):
        return 16
    if codec in ("pcm_s24le", "pcm_s24be"):
        return 24
    if codec in ("pcm_s32le", "pcm_f32le"):
        return 32
    raise CliError(f"unknown codec {codec}")


def aiff_target_from_stream(stream: dict) -> tuple[str, int]:
    """Return (pcm_s16be|pcm_s24be, sample_rate) under the Pioneer 24/48 ceiling.

    Never raises bit depth or sample rate above the source (within the ceiling).
    """
    fmt = str(stream.get("sample_fmt") or "")
    raw = stream.get("bits_per_raw_sample")
    bits: int | None = None
    if raw not in (None, "", "0", "N/A"):
        try:
            bits = int(raw)
        except (TypeError, ValueError):
            bits = None
    if bits is None and fmt in ("s16", "s16p"):
        bits = 16
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

    try:
        rate = int(float(stream.get("sample_rate") or 0))
    except (TypeError, ValueError):
        rate = 0
    if rate > 48000:
        rate = 48000
    elif rate in (44100, 48000):
        pass
    else:
        rate = 44100

    codec = "pcm_s16be" if bits == 16 else "pcm_s24be"
    return codec, rate


def classify_source(
    path: Path, stream: dict, *, output_format: str = "wav"
) -> tuple[str, bool]:
    """Return (ffmpeg_codec or 'copy', is_copy) for the selected output format."""
    ext = path.suffix.lower()
    codec_name = str(stream.get("codec_name") or "")
    if ext not in SUPPORTED_LOSSLESS_EXT:
        raise CliError(f"unsupported format: {path}")
    if output_format == "aiff":
        if ext in AIFF_EXT and is_cdj_safe_aiff(path):
            return "copy", True
        if ext in ALAC_EXT and codec_name != "alac":
            raise CliError(f"unsupported format: {path} (expected ALAC)")
        codec, _rate = aiff_target_from_stream(stream)
        return codec, False
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
    *,
    output_format: str = "wav",
) -> tuple[Plan | None, list[str]]:
    if output_format not in ("wav", "aiff"):
        output_format = "wav"
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
        dest_name = dest_name_for(source_path, output_format=output_format)
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
            codec, is_copy = classify_source(
                item.source_path, stream, output_format=output_format
            )
        except CliError as exc:
            errors.append(str(exc))
            continue
        item.copy_wav = is_copy
        item.codec = None if is_copy else codec
        in_place = same_file(item.source_path, item.dest_path)
        if output_format == "aiff":
            if in_place:
                cover = extract_cover_jpeg(item.source_path)
                if _is_canonical_aiff_output(item.dest_path, item.source_el, cover):
                    item.noop = True
                else:
                    errors.append(
                        "refusing to convert in place "
                        f"(source is not a canonical AIFF output): {item.source_path}"
                    )
            else:
                item.noop = False
        else:
            item.noop = is_copy and in_place
            if (not is_copy) and in_place:
                errors.append(
                    "refusing to convert in place "
                    f"(source is not CDJ-safe WAV): {item.source_path}"
                )

    suffix = AIFF_SUFFIX if output_format == "aiff" else WAV_SUFFIX
    wav_playlist_name = f"{playlist_name}{suffix}"
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


def run_ffmpeg(
    source: Path,
    dest: Path,
    codec: str,
    force: bool,
    *,
    sample_rate: int | None = None,
) -> None:
    """Encode dest as CDJ-safe WAV or Pioneer-ceiling AIFF PCM (no ID3)."""
    exe = tool_path("ffmpeg")
    if exe is None:
        raise CliError(
            "ffmpeg not found on PATH (install with: brew install ffmpeg)"
        )
    is_aiff = dest.suffix.lower() == ".aiff"
    if is_aiff:
        audio_codec = codec if codec in ("pcm_s16be", "pcm_s24be") else "pcm_s16be"
        rate = sample_rate if sample_rate in (44100, 48000) else 44100
    else:
        audio_codec = "pcm_s16le"
        rate = CDJ_SAFE_SAMPLE_RATE
        del codec
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
            str(rate),
            "-ac",
            str(CDJ_SAFE_CHANNELS),
            "-c:a",
            audio_codec,
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
    if is_aiff:
        if not is_cdj_safe_aiff(dest):
            raise CliError(
                f"ffmpeg produced a non-CDJ-safe AIFF for {source}: {dest}"
            )
    elif not is_cdj_safe_wav(dest):
        raise CliError(
            f"ffmpeg produced a non-CDJ-safe WAV for {source}: {dest}"
        )


def write_aiff_output(
    source: Path,
    dest: Path,
    source_el: ET.Element,
    *,
    passthrough: bool,
    codec: str | None,
) -> None:
    """Atomically write AIFF: PCM then ID3, validate, os.replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cover = extract_cover_jpeg(source)
    fd, tmp_name = tempfile.mkstemp(
        dir=dest.parent, prefix=".aiff-", suffix=".tmp.aiff"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        if passthrough:
            _normalize_aiff_audio_chunks(source, tmp)
        else:
            if not codec:
                raise CliError(f"no codec planned for {source}")
            # Probe for rate when needed
            probe = run_ffprobe(source)
            stream = first_stream(probe) or {}
            _codec, rate = aiff_target_from_stream(stream)
            run_ffmpeg(source, tmp, codec or _codec, force=True, sample_rate=rate)
            if not is_cdj_safe_aiff(tmp):
                raise CliError(f"AIFF audio stage failed for {source}")
            # Re-normalize in case the muxer added anything unexpected.
            fd2, tmp2_name = tempfile.mkstemp(
                dir=dest.parent, prefix=".aiff-norm-", suffix=".tmp.aiff"
            )
            os.close(fd2)
            tmp2 = Path(tmp2_name)
            try:
                _normalize_aiff_audio_chunks(tmp, tmp2)
                os.replace(tmp2, tmp)
            except Exception:
                try:
                    tmp2.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        write_aiff_id3(tmp, source_el, cover)
        if not _is_canonical_aiff_output(tmp, source_el, cover):
            raise CliError(f"AIFF failed canonical validation for {source}")
        os.replace(tmp, dest)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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
            is_aiff = item.dest_path.suffix.lower() == ".aiff"
            if item.noop:
                bar.update(i, "skip", name)
                stats.skipped += 1
                continue
            if is_aiff:
                cover = extract_cover_jpeg(item.source_path)
                if not force and _is_canonical_aiff_output(
                    item.dest_path, item.source_el, cover
                ):
                    bar.update(i, "skip", name)
                    stats.skipped += 1
                    continue
                action = "copy" if item.copy_wav else "convert"
                bar.update(i, action, name)
                write_aiff_output(
                    item.source_path,
                    item.dest_path,
                    item.source_el,
                    passthrough=item.copy_wav,
                    codec=item.codec,
                )
                if item.copy_wav:
                    stats.copied += 1
                else:
                    stats.converted += 1
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
            "pcm_s16be": 16,
            "pcm_s24le": 24,
            "pcm_s24be": 24,
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
    kind = "AIFF File" if dest_path.suffix.lower() == ".aiff" else "WAV File"
    clone.set("Kind", kind)
    clone.set("Size", size)
    clone.set("BitRate", bitrate)
    clone.set("SampleRate", sample_rate)
    return clone


def refresh_track(
    existing: ET.Element,
    source_el: ET.Element,
    dest_path: Path,
    dest_location: str,
) -> None:
    """Update an existing collection TRACK from source_el; keep TrackID."""
    tid = existing.get("TrackID", "")
    # Replace children and attributes from a fresh clone, then restore TrackID.
    refreshed = clone_track(source_el, tid, dest_path, dest_location)
    existing.clear()
    existing.attrib.update(refreshed.attrib)
    existing.set("TrackID", tid)
    existing.set("Location", dest_location)
    for child in list(refreshed):
        existing.append(child)


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
            refresh_track(
                existing, item.source_el, item.dest_path, item.dest_location
            )
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
        print(f"{unique_n} audio files → {plan.playlist_dir}{missing}")
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
        parts.append(f"{unique_n} audio files")
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
    output_format: str = "wav",
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
        output_format=output_format,
    )
    errors.extend(plan_errors)
    return plan, errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    need_wizard = args.xml is None or args.playlist is None
    output_format = args.format
    if need_wizard:
        if not sys.stdin.isatty():
            print(
                "error: --xml and --playlist are required when not running interactively",
                file=sys.stderr,
            )
            return 2
        try:
            xml_path, playlist_refs, wav_dir, output, output_format = prompt_wizard(args)
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
            output_format=output_format,
        )
        if rc != 0:
            return rc
    if not args.dry_run:
        print_import_hints(abs_path(output), output_format=output_format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
