#!/usr/bin/env python3
"""Convert a Rekordbox playlist's lossless tracks to WAV and write import XML."""

from __future__ import annotations

import argparse
import copy
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Collection

import convert_plan
import ffmpeg_tools
import xml_output
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
    _rewrite_wav_pcm,
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
    iter_playlist_nodes,
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


# Re-exports for callers (launcher / GUI / tests that import rb.*).
FFPROBE_TIMEOUT_S = ffmpeg_tools.FFPROBE_TIMEOUT_S
FFMPEG_SOXR_TIMEOUT_S = ffmpeg_tools.FFMPEG_SOXR_TIMEOUT_S
FFMPEG_CONVERT_TIMEOUT_S = ffmpeg_tools.FFMPEG_CONVERT_TIMEOUT_S
FFMPEG_COVER_TIMEOUT_S = ffmpeg_tools.FFMPEG_COVER_TIMEOUT_S
CODEC_BY_DEPTH = ffmpeg_tools.CODEC_BY_DEPTH
tool_path = ffmpeg_tools.tool_path
require_tools = ffmpeg_tools.require_tools
run_ffprobe = ffmpeg_tools.run_ffprobe
first_stream = ffmpeg_tools.first_stream
pcm_codec_for_stream = ffmpeg_tools.pcm_codec_for_stream
bit_depth_of_codec = ffmpeg_tools.bit_depth_of_codec
ffmpeg_supports_soxr = ffmpeg_tools.ffmpeg_supports_soxr


# Re-exports from convert_plan for callers.
DEFAULT_WAV_DIR = convert_plan.DEFAULT_WAV_DIR
DEFAULT_OUTPUT = convert_plan.DEFAULT_OUTPUT
WAV_SUFFIX = convert_plan.WAV_SUFFIX
SUPPORTED_LOSSLESS_EXT = convert_plan.SUPPORTED_LOSSLESS_EXT
WAV_EXT = convert_plan.WAV_EXT
ALAC_EXT = convert_plan.ALAC_EXT
AIFF_SUFFIX = convert_plan.AIFF_SUFFIX
AIFF_EXT = convert_plan.AIFF_EXT
Progress = convert_plan.Progress
PlannedTrack = convert_plan.PlannedTrack
Plan = convert_plan.Plan
ConvertStats = convert_plan.ConvertStats
cached_cover_jpeg = convert_plan.cached_cover_jpeg
abs_path = convert_plan.abs_path
dest_name_for = convert_plan.dest_name_for
playlist_dir_name = convert_plan.playlist_dir_name
collision_key = convert_plan.collision_key
resolve_existing_file = convert_plan.resolve_existing_file
same_file = convert_plan.same_file
target_from_stream = convert_plan.target_from_stream
pcm_codec_for_depth = convert_plan.pcm_codec_for_depth
classify_source = convert_plan.classify_source
build_plan = convert_plan.build_plan
run_ffmpeg = convert_plan.run_ffmpeg
write_aiff_output = convert_plan.write_aiff_output
convert_unique = convert_plan.convert_unique

# Re-exports from xml_output for callers.
next_track_id = xml_output.next_track_id
ensure_root_node = xml_output.ensure_root_node
find_or_create_wav_playlist = xml_output.find_or_create_wav_playlist
share_output_root = xml_output.share_output_root
rewrite_counts = xml_output.rewrite_counts
probe_dest_tech = xml_output.probe_dest_tech
clone_track = xml_output.clone_track
refresh_track = xml_output.refresh_track
playlist_keys = xml_output.playlist_keys
apply_xml = xml_output.apply_xml
atomic_write_xml = xml_output.atomic_write_xml

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a Rekordbox playlist to stereo PCM WAV or AIFF "
            "(selectable max bit depth / sample rate; never upconvert) "
            "and write import XML. "
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
        help="Output Rekordbox XML (default: ./output/rekordbox-import.xml)",
    )
    parser.add_argument(
        "--format",
        choices=("wav", "aiff"),
        default="wav",
        help=(
            "Output format: wav or aiff "
            "(quality ceiling from --bit-depth / --sample-rate; never upconvert)"
        ),
    )
    parser.add_argument(
        "--bit-depth",
        type=int,
        choices=(16, 24),
        default=24,
        help=(
            "Maximum bit depth (16 or 24). Default 24. "
            "16-bit tracks are not upconverted to 24-bit."
        ),
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        choices=(44100, 48000),
        default=48000,
        help=(
            "Maximum sample rate (44100 or 48000). Default 48000. "
            "44.1 kHz tracks are not upconverted to 48 kHz."
        ),
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
) -> tuple[Path, list[tuple[str | None, str]], Path, Path, str, int, int]:
    xml_path = prompt_xml_path(args.xml)
    root = load_dj_playlists(xml_path)
    names = prompt_playlists(root, args.playlist)
    wav_dir, output = prompt_paths(args.wav_dir, args.output)
    output_format = prompt_line("Format (wav/aiff)", getattr(args, "format", "wav"))
    output_format = output_format.strip().lower()
    while output_format not in ("wav", "aiff"):
        print("  choose wav or aiff", file=sys.stderr)
        output_format = prompt_line("Format (wav/aiff)", "wav").strip().lower()
    bit_s = prompt_line(
        "Max bit depth (16/24)", str(getattr(args, "bit_depth", 24))
    )
    while bit_s.strip() not in ("16", "24"):
        print("  choose 16 or 24", file=sys.stderr)
        bit_s = prompt_line("Max bit depth (16/24)", "24")
    rate_s = prompt_line(
        "Max sample rate (44100/48000)", str(getattr(args, "sample_rate", 48000))
    )
    while rate_s.strip() not in ("44100", "48000"):
        print("  choose 44100 or 48000", file=sys.stderr)
        rate_s = prompt_line("Max sample rate (44100/48000)", "48000")
    return (
        xml_path,
        names,
        wav_dir,
        output,
        output_format,
        int(bit_s.strip()),
        int(rate_s.strip()),
    )


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
    max_bit_depth: int = 24,
    max_sample_rate: int = 48000,
) -> int:
    plan, errors = prepare(
        xml_path,
        playlist_name,
        wav_dir,
        output,
        playlist_folder=playlist_folder,
        output_format=output_format,
        max_bit_depth=max_bit_depth,
        max_sample_rate=max_sample_rate,
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
        stats = convert_plan.convert_unique(
            plan, force=force, progress=sys.stderr.isatty()
        )
        stats.appended = xml_output.apply_xml(plan)
        xml_output.atomic_write_xml(plan.output_root, plan.output)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print_summary(plan, stats, dry_run=False)
    return 0


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
    max_bit_depth: int = 24,
    max_sample_rate: int = 48000,
    track_keys: Collection[str] | None = None,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[Plan | None, list[str]]:
    errors: list[str] = []
    errors.extend(ffmpeg_tools.require_tools())
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

    if track_keys is not None:
        allowed = set(track_keys)
        playlist_el = copy.deepcopy(playlist_el)
        for entry in list(playlist_el.findall("TRACK")):
            if (entry.get("Key") or "") not in allowed:
                playlist_el.remove(entry)

    output_path = convert_plan.abs_path(output)
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

    plan, plan_errors = convert_plan.build_plan(
        source_root,
        playlist_el,
        resolved_name,
        wav_dir,
        output_path,
        output_root,
        output_existed,
        output_format=output_format,
        max_bit_depth=max_bit_depth,
        max_sample_rate=max_sample_rate,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )
    errors.extend(plan_errors)
    return plan, errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    need_wizard = args.xml is None or args.playlist is None
    output_format = args.format
    max_bit_depth = args.bit_depth
    max_sample_rate = args.sample_rate
    if need_wizard:
        if not sys.stdin.isatty():
            print(
                "error: --xml and --playlist are required when not running interactively",
                file=sys.stderr,
            )
            return 2
        try:
            (
                xml_path,
                playlist_refs,
                wav_dir,
                output,
                output_format,
                max_bit_depth,
                max_sample_rate,
            ) = prompt_wizard(args)
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
            max_bit_depth=max_bit_depth,
            max_sample_rate=max_sample_rate,
        )
        if rc != 0:
            return rc
    if not args.dry_run:
        print_import_hints(abs_path(output), output_format=output_format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
