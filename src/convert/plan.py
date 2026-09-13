"""Build conversion plans, cover cache, batch unique sharing, encode wrappers."""

from __future__ import annotations

import os
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Callable

import converter_manifest
import ffmpeg_tools
from cdj_aiff import extract_cover_jpeg
from cli_error import CancelledError, CliError
from convert.encode import (
    run_ffmpeg as _encode_run_ffmpeg,
    write_aiff_output as _encode_write_aiff_output,
)
from convert.format_policy import classify_source, inplace_noop_and_error
from convert.models import Plan, PlannedTrack
from convert.paths import (
    abs_path,
    format_media_dir,
    parse_duration_seconds,
    preferred_relative_dest,
    resolve_existing_file,
    same_file,
    source_key,
)
from convert.quality import (
    coerce_bit_depth,
    coerce_output_format,
    coerce_sample_rate,
)
from converter_manifest import ConverterManifest
from rekordbox_xml import decode_location, encode_location, resolve_playlist_tracks

DEFAULT_WAV_DIR = Path("output")
DEFAULT_OUTPUT = Path("output") / "rekordbox-import.xml"
WAV_SUFFIX = " [WAV]"
AIFF_SUFFIX = " [AIFF]"

CONVERT_WORKERS_MIN = 1
CONVERT_WORKERS_MAX = 4


def default_convert_workers() -> int:
    """Worker count from cpu_count, at least 1, at most 4."""
    n = os.cpu_count() or 4
    return max(CONVERT_WORKERS_MIN, min(n, CONVERT_WORKERS_MAX))


CONVERT_WORKERS = default_convert_workers()


def convert_worker_count(n_items: int, *, workers: int | None = None) -> int:
    """Clamp requested or default workers to 1..4 and to the number of items."""
    base = CONVERT_WORKERS if workers is None else int(workers)
    capped = max(CONVERT_WORKERS_MIN, min(base, CONVERT_WORKERS_MAX))
    if n_items <= 0:
        return CONVERT_WORKERS_MIN
    return max(CONVERT_WORKERS_MIN, min(capped, n_items))


def cached_cover_jpeg(
    source: Path,
    cache: dict[Path, bytes | None],
    *,
    lock: threading.Lock | None = None,
    cancel_event: threading.Event | None = None,
) -> bytes | None:
    """Extract cover once per source path for the duration of a convert run."""
    if lock is None:
        if source not in cache:
            cache[source] = extract_cover_jpeg(source, cancel_event=cancel_event)
        return cache[source]
    with lock:
        if source not in cache:
            cache[source] = extract_cover_jpeg(source, cancel_event=cancel_event)
        return cache[source]


def run_ffmpeg(*args, **kwargs):
    """Encode via convert.encode; inject CONVERT_WORKERS for timeout scaling."""
    kwargs.setdefault("convert_workers", CONVERT_WORKERS)
    return _encode_run_ffmpeg(*args, **kwargs)


def write_aiff_output(*args, **kwargs):
    """Write AIFF via convert.encode; inject cover cache + worker defaults."""
    kwargs.setdefault("cover_lookup", cached_cover_jpeg)
    kwargs.setdefault("convert_workers", CONVERT_WORKERS)
    return _encode_write_aiff_output(*args, **kwargs)


def _shutdown_cancelable_pool(
    pool: ThreadPoolExecutor,
    cancel_event: threading.Event | None,
) -> None:
    """Shut down a probe/preview pool; cancel pending work if cancelled.

    Encode pools (convert_unique) wait in-flight and must not use this helper.
    """
    cancelled = cancel_event is not None and cancel_event.is_set()
    pool.shutdown(wait=not cancelled, cancel_futures=cancelled)


def collect_batch_unique(plans: list[Plan]) -> list[PlannedTrack]:
    """One PlannedTrack per (source_key, format) across plans; first wins."""
    seen: set[tuple[str, str]] = set()
    out: list[PlannedTrack] = []
    for plan in plans:
        fmt = plan.output_format
        for item in plan.unique:
            key = (source_key(item.source_path), fmt)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def share_cover_caches(plans: list[Plan]) -> None:
    """Point every plan at the first plan's cover_cache (merged)."""
    if len(plans) < 2:
        return
    host = plans[0]
    for plan in plans[1:]:
        host.cover_cache.update(plan.cover_cache)
        plan.cover_cache = host.cover_cache


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
    max_bit_depth: int = 24,
    max_sample_rate: int = 48000,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    manifest: ConverterManifest | None = None,
    workers: int | None = None,
) -> tuple[Plan | None, list[str]]:
    output_format = coerce_output_format(output_format)
    max_bit_depth = coerce_bit_depth(max_bit_depth)
    max_sample_rate = coerce_sample_rate(max_sample_rate)
    errors: list[str] = []
    tracks_el, resolve_errors = resolve_playlist_tracks(source_root, playlist_el)
    errors.extend(resolve_errors)

    wav_dir_abs = abs_path(wav_dir)
    if manifest is None:
        manifest = converter_manifest.empty_manifest()

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
        preferred = preferred_relative_dest(
            el,
            output_format=output_format,
            stem_fallback=source_path.stem,
        )
        rel = converter_manifest.reserve_relative_dest(
            manifest,
            source_key=source_key(source_path),
            output_format=output_format,
            preferred=preferred,
            wav_dir=wav_dir_abs,
            source_path=source_path,
        )
        dest_path = wav_dir_abs.joinpath(*PurePosixPath(rel).parts)
        dest_name = dest_path.name
        dest_location = encode_location(dest_path)
        planned.append(
            PlannedTrack(
                source_el=el,
                source_path=source_path,
                dest_path=dest_path,
                dest_location=dest_location,
                dest_name=dest_name,
                codec=None,
                passthrough=False,
                noop=False,
                output_format=output_format,
            )
        )

    unique: list[PlannedTrack] = []
    unique_sources: dict[str, PlannedTrack] = {}
    for item in planned:
        sk = source_key(item.source_path)
        if sk in unique_sources:
            continue
        unique_sources[sk] = item
        unique.append(item)

    total = len(unique)
    cover_cache: dict[Path, bytes | None] = {}
    cover_lock = threading.Lock()
    state_lock = threading.Lock()
    completed = 0

    def _record_error(message: str) -> None:
        with state_lock:
            errors.append(message)

    def _finish_progress(item: PlannedTrack) -> None:
        nonlocal completed
        if on_progress is None:
            return
        with state_lock:
            completed += 1
            on_progress(completed, total, "prepare", item.dest_name)

    def probe_one(item: PlannedTrack) -> None:
        if cancel_event is not None and cancel_event.is_set():
            return
        try:
            probe = ffmpeg_tools.run_ffprobe(
                item.source_path, cancel_event=cancel_event
            )
        except CancelledError:
            return
        except CliError as exc:
            _record_error(str(exc))
            _finish_progress(item)
            return
        if cancel_event is not None and cancel_event.is_set():
            return
        stream = ffmpeg_tools.first_stream(probe)
        if stream is None:
            _record_error(
                f"unsupported format: {item.source_path} (no audio stream)"
            )
            _finish_progress(item)
            return
        try:
            codec, is_copy, bits, rate = classify_source(
                item.source_path,
                stream,
                output_format=output_format,
                max_bit_depth=max_bit_depth,
                max_sample_rate=max_sample_rate,
            )
        except CliError as exc:
            _record_error(str(exc))
            _finish_progress(item)
            return
        item.passthrough = is_copy
        item.codec = None if is_copy else codec
        item.bit_depth = bits
        item.sample_rate = rate
        item.duration_seconds = parse_duration_seconds(probe)
        cover: bytes | None = None
        if output_format == "aiff" and same_file(item.source_path, item.dest_path):
            cover = cached_cover_jpeg(
                item.source_path,
                cover_cache,
                lock=cover_lock,
                cancel_event=cancel_event,
            )
        noop, inplace_error = inplace_noop_and_error(
            item,
            output_format=output_format,
            is_copy=is_copy,
            cover=cover,
            bit_depth=bits,
            sample_rate=rate,
        )
        item.noop = noop
        if inplace_error is not None:
            _record_error(inplace_error)
        _finish_progress(item)

    if unique:
        probe_workers = convert_worker_count(len(unique), workers=workers)
        pool = ThreadPoolExecutor(max_workers=probe_workers)
        try:
            futures = [pool.submit(probe_one, item) for item in unique]
            for fut in as_completed(futures):
                fut.result()
                if cancel_event is not None and cancel_event.is_set():
                    break
        finally:
            _shutdown_cancelable_pool(pool, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            return None, []

    suffix = AIFF_SUFFIX if output_format == "aiff" else WAV_SUFFIX
    wav_playlist_name = f"{playlist_name}{suffix}"
    plan = Plan(
        playlist_name=playlist_name,
        wav_playlist_name=wav_playlist_name,
        library_dir=wav_dir_abs,
        media_dir=format_media_dir(wav_dir_abs, output_format),
        output=output,
        tracks=planned,
        unique=unique,
        source_root=source_root,
        output_root=output_root,
        output_existed=output_existed,
        warnings=warnings,
        output_format=output_format,
        max_bit_depth=max_bit_depth,
        max_sample_rate=max_sample_rate,
        cover_cache=cover_cache,
    )
    if errors:
        return plan, errors
    return plan, []
