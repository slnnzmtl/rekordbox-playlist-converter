#!/usr/bin/env python3
"""Convert a Rekordbox playlist's lossless tracks to WAV and write import XML."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import convert.write as convert_write
import converter_manifest
from cli_error import CliError
from convert import (
    ConvertStats,
    Plan,
    PreparedConversion,
    execute_prepared,
    prepare,
    prepare_batch,
)
from convert.models import (
    conversion_exit_code,
    format_conversion_counts,
    stats_for_playlist,
)
from convert.plan import DEFAULT_OUTPUT, DEFAULT_WAV_DIR
from convert.preview import (
    build_conversion_preview,
    insufficient_output_space_message,
    preview_write_bytes,
)
from gui_prefs import import_xml_path
from rekordbox_xml import (
    discover_xml_candidates,
    iter_playlists,
    load_dj_playlists,
    parse_playlist_selection,
    playlist_label,
    playlist_track_count,
)


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
        default=None,
        help=(
            "Advanced override for the import XML path "
            "(default: <wav-dir>/rekordbox-import.xml)"
        ),
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
            "44.1 kHz tracks are not upconverted to 48 kHz. "
            "Other rates are snapped to 44100 when allowed by the ceiling."
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


def resolve_cli_output(wav_dir: Path, output: Path | None) -> Path:
    """Return explicit --output, or <wav-dir>/rekordbox-import.xml when omitted."""
    if output is not None:
        return output
    return import_xml_path(wav_dir)


def prompt_paths(wav_dir: Path, output: Path | None) -> tuple[Path, Path]:
    print()
    wav_s = prompt_line("Audio directory", str(wav_dir))
    chosen_wav = Path(wav_s).expanduser()
    if output is not None:
        out_s = prompt_line("Output XML", str(output))
        return chosen_wav, Path(out_s).expanduser()
    return chosen_wav, resolve_cli_output(chosen_wav, None)


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


def run_convert_batch(
    xml_path: Path,
    playlist_refs: list[tuple[str | None, str]],
    wav_dir: Path,
    output: Path,
    *,
    force: bool,
    dry_run: bool,
    output_format: str = "wav",
    max_bit_depth: int = 24,
    max_sample_rate: int = 48000,
    source_root: ET.Element | None = None,
) -> int:
    """Prepare all playlists, convert unique (source_key, format) once, apply XML."""
    if not playlist_refs:
        return 1
    library_error = converter_manifest.validate_library_folder(wav_dir)
    if library_error is not None:
        print(library_error, file=sys.stderr)
        return 1
    if source_root is None:
        try:
            source_root = load_dj_playlists(xml_path)
        except CliError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    prepared, errors = prepare_batch(
        xml_path,
        playlist_refs,
        wav_dir,
        output,
        output_format=output_format,
        max_bit_depth=max_bit_depth,
        max_sample_rate=max_sample_rate,
        force=force,
        source_root=source_root,
    )
    if errors:
        print_errors(errors)
        return 1
    assert prepared is not None
    if prepared.skipped:
        print_warnings(prepared.skipped)

    plans = prepared.plans
    preview = prepared.preview

    if dry_run:
        print_conversion_preview(plans, preview)
        return 0

    space_issue = insufficient_output_space_message(
        wav_dir,
        preview_write_bytes(preview),
    )
    if space_issue is not None:
        print(space_issue, file=sys.stderr)
        return 1

    try:
        stats = convert_write.execute_prepared(
            prepared,
            force=force,
            progress=sys.stderr.isatty(),
        )
        for i, plan in enumerate(plans):
            if len(plans) > 1:
                print()
                folder, name = playlist_refs[i]
                label = playlist_label(folder, name) if folder else name
                print(f"=== {label} ({i + 1}/{len(plans)}) ===")
            appended = (
                stats.appended_by_plan[i] if i < len(stats.appended_by_plan) else 0
            )
            plan_stats = stats_for_playlist(
                stats,
                plan.wav_playlist_name,
                appended=appended,
            )
            print_summary(plan, plan_stats, dry_run=False)
    except OSError as exc:
        print(f"cannot write converter manifest: {exc}", file=sys.stderr)
        return 1
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return conversion_exit_code(stats)


def print_errors(errors: list[str]) -> None:
    for err in errors:
        print(err, file=sys.stderr)


def print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def print_conversion_preview(plans: list[Plan], preview) -> None:
    """Print shared ConversionPreview for CLI --dry-run (read-only)."""
    print(
        f"{preview.unique_outputs} unique output file(s) · "
        f"{preview.selected} selected · "
        f"{preview.resolved} resolved · "
        f"{preview.duplicates} duplicate(s) · "
        f"{preview.missing} missing"
    )
    print()
    print("Format directory:")
    print(plans[0].media_dir)
    print()
    if preview.items:
        print("Inputs:")
        for item in preview.items:
            quality = f"{item.bit_depth}-bit / {item.sample_rate} Hz"
            print(
                f"{item.source_display}  {item.action}  {quality}  {item.size_display}"
            )
        print()
    print("New playlist:")
    for plan in plans:
        print(plan.wav_playlist_name)
    print()
    print("Import XML:")
    print(plans[0].output)


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
        print(f"{unique_n} audio files → {plan.media_dir}{missing}")
        print()
        print("Output:")
        print(plan.output)
        print()
        print("New playlist:")
        print(plan.wav_playlist_name)
        return
    assert stats is not None
    print("Converted:")
    parts = format_conversion_counts(stats, missing=len(plan.warnings))
    if not parts:
        parts.append(f"{unique_n} audio files")
    print(f"{', '.join(parts)} → {plan.media_dir}")
    print()
    print("Output:")
    print(plan.output)
    print()
    print("New playlist:")
    if stats.appended:
        print(f"{plan.wav_playlist_name} (+{stats.appended} entries)")
    else:
        print(plan.wav_playlist_name)
    if stats.conflicts:
        print()
        print("Conflicts:")
        for name in stats.conflicts:
            print(name)
    if stats.state_changed:
        print()
        print("State changed (refresh preview):")
        for name in stats.state_changed:
            print(name)
    if stats.errors:
        print()
        print("Failed:")
        for err in stats.errors:
            print(err)


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
        output = resolve_cli_output(wav_dir, args.output)

    try:
        shared_root = load_dj_playlists(xml_path)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    from convert.paths import abs_path

    rc = run_convert_batch(
        xml_path,
        playlist_refs,
        wav_dir,
        output,
        force=args.force,
        dry_run=args.dry_run,
        output_format=output_format,
        max_bit_depth=max_bit_depth,
        max_sample_rate=max_sample_rate,
        source_root=shared_root,
    )
    if rc != 0:
        return rc
    if not args.dry_run:
        print_import_hints(abs_path(output), output_format=output_format)
    return 0


__all__ = [
    "CliError",
    "ConvertStats",
    "DEFAULT_OUTPUT",
    "DEFAULT_WAV_DIR",
    "Plan",
    "PreparedConversion",
    "build_conversion_preview",
    "execute_prepared",
    "insufficient_output_space_message",
    "iter_playlists",
    "load_dj_playlists",
    "main",
    "parse_args",
    "prepare",
    "prepare_batch",
    "preview_write_bytes",
    "prompt_paths",
    "prompt_wizard",
    "run_convert_batch",
]


if __name__ == "__main__":
    raise SystemExit(main())
