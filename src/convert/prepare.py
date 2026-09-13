"""Prepare single playlists and multi-playlist batches for conversion."""

from __future__ import annotations

import copy
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Collection

import converter_manifest
import ffmpeg_tools
from cli_error import CancelledError, CliError
from convert.models import Plan, PreparedConversion
from convert.paths import abs_path
from convert.plan import build_plan, collect_batch_unique, share_cover_caches
from convert.preview import build_conversion_preview
from convert.quality import require_bit_depth, require_output_format, require_sample_rate
from rekordbox_xml import (
    load_dj_playlists,
    resolve_playlist,
    skeleton_from,
)
from xml_output import share_output_root


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
    source_root: ET.Element | None = None,
    manifest: converter_manifest.ConverterManifest | None = None,
    workers: int | None = None,
) -> tuple[Plan | None, list[str]]:
    errors: list[str] = []
    errors.extend(ffmpeg_tools.require_tools())
    if not xml_path.is_file():
        errors.append(f"source XML not found: {xml_path}")
        return None, errors
    try:
        output_format = require_output_format(output_format)
        max_bit_depth = require_bit_depth(max_bit_depth)
        max_sample_rate = require_sample_rate(max_sample_rate)
    except CliError as exc:
        errors.append(str(exc))
        return None, errors
    if source_root is None:
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

    if manifest is None:
        try:
            manifest = converter_manifest.load_manifest(wav_dir)
        except CliError as exc:
            errors.append(str(exc))
            return None, errors

    plan, plan_errors = build_plan(
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
        manifest=manifest,
        workers=workers,
    )
    errors.extend(plan_errors)
    return plan, errors


def prepare_batch(
    xml_path: Path,
    playlist_refs: list[tuple[str | None, str]],
    wav_dir: Path,
    output: Path,
    *,
    output_format: str = "wav",
    max_bit_depth: int = 24,
    max_sample_rate: int = 48000,
    force: bool = False,
    track_keys_by_playlist: dict[tuple[str | None, str], Collection[str]] | None = None,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    source_root: ET.Element | None = None,
    manifest: converter_manifest.ConverterManifest | None = None,
    on_playlist_preparing: Callable[[str, int, int], None] | None = None,
    workers: int | None = None,
    prepare_fn: Callable[..., tuple[Plan | None, list[str]]] | None = None,
    share_output_root_fn: Callable[[list[Plan]], None] | None = None,
    collect_batch_unique_fn: Callable[[list[Plan]], list] | None = None,
    share_cover_caches_fn: Callable[[list[Plan]], None] | None = None,
    build_conversion_preview_fn: Callable[..., object] | None = None,
) -> tuple[PreparedConversion | None, list[str]]:
    """Prepare playlists, share root/covers, collect unique items, build preview.

    Optional *_fn args let hosts (rb facade) inject patched module-level seams.
    """
    prepare_one = prepare if prepare_fn is None else prepare_fn
    share_root = share_output_root if share_output_root_fn is None else share_output_root_fn
    collect_unique = (
        collect_batch_unique if collect_batch_unique_fn is None else collect_batch_unique_fn
    )
    share_covers = (
        share_cover_caches if share_cover_caches_fn is None else share_cover_caches_fn
    )
    build_preview = (
        build_conversion_preview
        if build_conversion_preview_fn is None
        else build_conversion_preview_fn
    )
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("conversion cancelled")

    if manifest is None:
        try:
            manifest = converter_manifest.load_manifest(wav_dir)
        except CliError as exc:
            return None, [str(exc)]

    plans: list[Plan] = []
    skipped: list[str] = []
    total = len(playlist_refs)
    for i, (folder, name) in enumerate(playlist_refs):
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("conversion cancelled")
        if on_playlist_preparing is not None:
            on_playlist_preparing(name, i, total)
        track_keys = None
        if track_keys_by_playlist is not None:
            track_keys = track_keys_by_playlist[(folder, name)]
        plan, errors = prepare_one(
            xml_path,
            name,
            wav_dir,
            output,
            playlist_folder=folder,
            output_format=output_format,
            max_bit_depth=max_bit_depth,
            max_sample_rate=max_sample_rate,
            track_keys=track_keys,
            on_progress=on_progress,
            cancel_event=cancel_event,
            source_root=source_root,
            manifest=manifest,
            workers=workers,
        )
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("conversion cancelled")
        if errors:
            return None, errors
        assert plan is not None
        if source_root is None:
            plan_root = getattr(plan, "source_root", None)
            if plan_root is not None:
                source_root = plan_root
        plans.append(plan)
        skipped.extend(plan.warnings)

    share_root(plans)
    items = collect_unique(plans)
    share_covers(plans)
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("conversion cancelled")

    preview = build_preview(
        plans,
        items,
        force=force,
        cancel_event=cancel_event,
        on_progress=on_progress,
        workers=workers,
    )
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("conversion cancelled")

    return (
        PreparedConversion(
            plans=plans,
            items=items,
            manifest=manifest,
            preview=preview,
            library_dir=wav_dir,
            output=output,
            skipped=skipped,
        ),
        [],
    )
