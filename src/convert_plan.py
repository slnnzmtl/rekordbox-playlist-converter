"""Plan and convert unique tracks (WAV/AIFF); no XML write or CLI wizard."""

from __future__ import annotations

import math
import os
import shutil
import threading
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Callable

import converter_manifest
import ffmpeg_tools
from cdj_aiff import (
    _is_canonical_aiff_from_info,
    _is_canonical_aiff_output,
    _info_is_cdj_safe_aiff,
    _parse_aiff_audio,
    extract_cover_jpeg,
    is_cdj_safe_aiff,
)
from cdj_wav import (
    is_cdj_safe_wav,
)
from cli_error import CancelledError, CliError
from convert.encode import (
    _copy_wav_atomic,
    pcm_codec_for_depth,
    run_ffmpeg as _encode_run_ffmpeg,
    write_aiff_output as _encode_write_aiff_output,
)
from convert.models import (
    ConversionPreview,
    ConversionPreviewItem,
    ConvertStats,
    Plan,
    PlannedTrack,
    Progress,
)
from convert.paths import (
    _format_size_mb,
    abs_path,
    collision_key,
    format_media_dir,
    parse_duration_seconds,
    preferred_relative_dest,
    resolve_existing_file,
    same_file,
    target_from_stream,
)
# Facade re-exports for rb_playlist_to_wav / callers (not used directly below).
from convert.paths import (
    format_dir_name as format_dir_name,
    playlist_dir_name as playlist_dir_name,
    sanitize_path_component as sanitize_path_component,
)
from convert.quality import (
    coerce_bit_depth,
    coerce_output_format,
    coerce_sample_rate,
)
from converter_manifest import ConverterManifest
from rekordbox_xml import (
    decode_location,
    encode_location,
    resolve_playlist_tracks,
)

DEFAULT_WAV_DIR = Path("output")
DEFAULT_OUTPUT = Path("output") / "rekordbox-import.xml"
WAV_SUFFIX = " [WAV]"
SUPPORTED_LOSSLESS_EXT = {".flac", ".aiff", ".aif", ".wav", ".wave", ".m4a", ".caf"}
WAV_EXT = {".wav", ".wave"}
ALAC_EXT = {".m4a", ".caf"}


AIFF_SUFFIX = " [AIFF]"
AIFF_EXT = {".aiff", ".aif"}

# Parallel unique-track converts (clamped when used).
CONVERT_WORKERS_MIN = 1
CONVERT_WORKERS_MAX = 5


def default_convert_workers() -> int:
    """Worker count from cpu_count, at least 1, at most 4."""
    n = os.cpu_count() or 4
    return max(CONVERT_WORKERS_MIN, min(n, 4))


CONVERT_WORKERS = default_convert_workers()


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


def convert_worker_count(n_items: int) -> int:
    """Clamp CONVERT_WORKERS to 1..5 and to the number of items."""
    capped = max(CONVERT_WORKERS_MIN, min(int(CONVERT_WORKERS), CONVERT_WORKERS_MAX))
    if n_items <= 0:
        return CONVERT_WORKERS_MIN
    return max(CONVERT_WORKERS_MIN, min(capped, n_items))


def source_key(path: Path) -> str:
    """NFC-normalized resolved path string identifying an existing source file."""
    return unicodedata.normalize("NFC", str(path.expanduser().resolve()))


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


def planned_action(
    plan: Plan,
    item: PlannedTrack,
    force: bool,
    *,
    cover_lock: threading.Lock | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Classify read-only action: reuse, copy, or transcode."""
    if item.noop:
        return "reuse"
    is_aiff = coerce_output_format(item.output_format) == "aiff"
    if not force:
        if is_aiff:
            # One dest parse: gate cover extract, then canonical ID3/cover check.
            try:
                dest_info = (
                    _parse_aiff_audio(item.dest_path)
                    if item.dest_path.is_file()
                    else None
                )
            except CliError:
                dest_info = None
            if dest_info is not None and _info_is_cdj_safe_aiff(
                dest_info,
                bit_depth=item.bit_depth,
                sample_rate=item.sample_rate,
            ):
                cover = cached_cover_jpeg(
                    item.source_path,
                    plan.cover_cache,
                    lock=cover_lock,
                    cancel_event=cancel_event,
                )
                if _is_canonical_aiff_from_info(
                    item.dest_path,
                    dest_info,
                    item.source_el,
                    cover,
                    bit_depth=item.bit_depth,
                    sample_rate=item.sample_rate,
                ):
                    return "reuse"
        elif is_cdj_safe_wav(
            item.dest_path,
            bit_depth=item.bit_depth,
            sample_rate=item.sample_rate,
        ):
            return "reuse"
    if item.copy_wav:
        return "copy"
    return "transcode"


def _preview_size(item: PlannedTrack, action: str) -> tuple[int | None, str]:
    """Return (bytes, display). Reuse uses dest size; else estimate PCM."""
    if action == "reuse":
        try:
            if item.dest_path.is_file():
                size = item.dest_path.stat().st_size
                return size, _format_size_mb(size)
        except OSError:
            pass
        return None, "—"
    duration = item.duration_seconds
    if duration is None or not math.isfinite(duration) or duration < 0:
        return None, "—"
    bytes_per_sample = 2 if item.bit_depth == 16 else 3
    estimated = int(duration * item.sample_rate * bytes_per_sample * 2)
    return estimated, _format_size_mb(estimated, approximate=True)


def build_conversion_preview(
    plans: list[Plan],
    items: list[PlannedTrack],
    force: bool,
    *,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[int, int, str, str], None] | None = None,
) -> ConversionPreview:
    """Build a read-only conversion preview from prepared plans and unique items."""
    if not plans:
        return ConversionPreview(
            selected=0,
            resolved=0,
            unique_outputs=0,
            duplicates=0,
            missing=0,
            items=[],
        )
    resolved = sum(len(plan.tracks) for plan in plans)
    missing = sum(len(plan.warnings) for plan in plans)
    selected = resolved + missing
    unique_outputs = len(items)
    duplicates = resolved - unique_outputs
    wav_dir = plans[0].wav_dir
    total = len(items)
    if total == 0:
        return ConversionPreview(
            selected=selected,
            resolved=resolved,
            unique_outputs=unique_outputs,
            duplicates=duplicates,
            missing=missing,
            items=[],
        )

    plan_by_item: dict[int, Plan] = {}
    for plan in plans:
        for item in plan.unique:
            plan_by_item.setdefault(id(item), plan)

    results: list[ConversionPreviewItem | None] = [None] * total
    cover_lock = threading.Lock()
    progress_lock = threading.Lock()
    completed = 0

    def classify_one(index: int, item: PlannedTrack) -> ConversionPreviewItem:
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("conversion cancelled during preview")
        plan = plan_by_item.get(id(item), plans[0])
        action = planned_action(
            plan,
            item,
            force,
            cover_lock=cover_lock,
            cancel_event=cancel_event,
        )
        try:
            relative_dest = item.dest_path.relative_to(wav_dir).as_posix()
        except ValueError:
            relative_dest = item.dest_path.name
        size_bytes, size_display = _preview_size(item, action)
        return ConversionPreviewItem(
            relative_dest=relative_dest,
            action=action,
            bit_depth=item.bit_depth,
            sample_rate=item.sample_rate,
            size_bytes=size_bytes,
            size_display=size_display,
            source_display=item.source_path.name,
        )

    workers = convert_worker_count(total)
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            pool.submit(classify_one, index, item): index
            for index, item in enumerate(items)
        }
        for fut in as_completed(futures):
            if cancel_event is not None and cancel_event.is_set():
                break
            index = futures[fut]
            try:
                results[index] = fut.result()
            except CancelledError:
                if cancel_event is not None:
                    cancel_event.set()
                break
            with progress_lock:
                completed += 1
                done = completed
            if on_progress is not None:
                on_progress(done, total, "preview", items[index].dest_name)
    finally:
        cancelled = cancel_event is not None and cancel_event.is_set()
        pool.shutdown(wait=not cancelled, cancel_futures=cancelled)
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("conversion cancelled during preview")

    preview_items = [item for item in results if item is not None]
    if len(preview_items) != total:
        raise CancelledError("conversion cancelled during preview")
    return ConversionPreview(
        selected=selected,
        resolved=resolved,
        unique_outputs=unique_outputs,
        duplicates=duplicates,
        missing=missing,
        items=preview_items,
    )


def preview_write_bytes(preview: ConversionPreview) -> int:
    """Bytes that copy/transcode will write; reuse needs no extra space."""
    total = 0
    for item in preview.items:
        if item.action == "reuse":
            continue
        if item.size_bytes is None:
            continue
        total += item.size_bytes
    return total


def _disk_usage_path(path: Path) -> Path:
    """Nearest existing ancestor suitable for shutil.disk_usage."""
    candidate = path.expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        pass
    while True:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            pass
        parent = candidate.parent
        if parent == candidate:
            return candidate
        candidate = parent


def insufficient_output_space_message(
    path: Path,
    required_bytes: int,
    *,
    disk_usage: Callable[[str | Path], object] | None = None,
) -> str | None:
    """Return an issue message when free space on path is below required_bytes."""
    if required_bytes <= 0:
        return None
    probe = disk_usage if disk_usage is not None else shutil.disk_usage
    try:
        usage = probe(_disk_usage_path(path))
        free = int(getattr(usage, "free"))
    except (OSError, TypeError, ValueError, AttributeError):
        return None
    if free >= required_bytes:
        return None
    needed = _format_size_mb(required_bytes, approximate=True)
    available = _format_size_mb(free)
    return (
        "Not enough free space in the output folder. "
        f"About {needed.removeprefix('≈ ')} needed, {available} available."
    )


def convert_unique(
    plan: Plan,
    force: bool,
    *,
    progress: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    items: list[PlannedTrack] | None = None,
) -> ConvertStats:
    stats = ConvertStats()
    plan.playlist_dir.mkdir(parents=True, exist_ok=True)
    items = list(items) if items is not None else plan.unique
    bar = Progress(len(items), progress, on_progress=on_progress)
    completed = 0
    stats_lock = threading.Lock()
    cover_lock = threading.Lock()

    def finish(action: str, name: str) -> None:
        nonlocal completed
        with stats_lock:
            completed += 1
            done = completed
        bar.update(done, action, name)

    def mark_succeeded(item: PlannedTrack) -> None:
        fmt = coerce_output_format(item.output_format)
        with stats_lock:
            stats.succeeded.add((source_key(item.source_path), fmt))

    def process_one(item: PlannedTrack) -> None:
        if cancel_event is not None and cancel_event.is_set():
            return
        name = item.dest_name
        is_aiff = coerce_output_format(item.output_format) == "aiff"
        action = planned_action(
            plan, item, force, cover_lock=cover_lock, cancel_event=cancel_event
        )
        if action == "reuse":
            with stats_lock:
                stats.skipped += 1
            mark_succeeded(item)
            finish("skip", name)
            return
        try:
            converter_manifest.ensure_dest_path_under_wav_dir(
                plan.wav_dir, item.dest_path
            )
            if is_aiff:
                write_aiff_output(
                    item.source_path,
                    item.dest_path,
                    item.source_el,
                    passthrough=item.copy_wav,
                    codec=item.codec,
                    bit_depth=item.bit_depth,
                    sample_rate=item.sample_rate,
                    cover_cache=plan.cover_cache,
                    cancel_event=cancel_event,
                    cover_lock=cover_lock,
                )
                with stats_lock:
                    if item.copy_wav:
                        stats.copied += 1
                    else:
                        stats.converted += 1
                mark_succeeded(item)
                finish("copy" if item.copy_wav else "convert", name)
                return
            if item.copy_wav:
                _copy_wav_atomic(
                    item.source_path, item.dest_path, cancel_event=cancel_event
                )
                with stats_lock:
                    stats.copied += 1
                mark_succeeded(item)
                finish("copy", name)
                return
            if not item.codec:
                raise CliError(f"no codec planned for {item.source_path}")
            run_ffmpeg(
                item.source_path,
                item.dest_path,
                item.codec,
                force=True,
                sample_rate=item.sample_rate,
                bit_depth=item.bit_depth,
                cancel_event=cancel_event,
                output_format=item.output_format,
            )
            with stats_lock:
                stats.converted += 1
            mark_succeeded(item)
            finish("convert", name)
        except CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 — collect all; report after pool
            with stats_lock:
                stats.errors.append(str(exc))
            finish("error", name)

    try:
        if not items:
            return stats
        workers = convert_worker_count(len(items))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(process_one, item) for item in items]
            for fut in as_completed(futures):
                fut.result()
    finally:
        bar.close()
    return stats


def classify_source(
    path: Path,
    stream: dict,
    *,
    output_format: str = "wav",
    max_bit_depth: int = 24,
    max_sample_rate: int = 48000,
) -> tuple[str, bool, int, int]:
    """Return (ffmpeg_codec or 'copy', is_copy, bit_depth, sample_rate)."""
    ext = path.suffix.lower()
    codec_name = str(stream.get("codec_name") or "")
    if ext not in SUPPORTED_LOSSLESS_EXT:
        raise CliError(f"unsupported format: {path}")
    bits, rate = target_from_stream(
        stream, max_bit_depth=max_bit_depth, max_sample_rate=max_sample_rate
    )
    if ext in ALAC_EXT and codec_name != "alac":
        raise CliError(f"unsupported format: {path} (expected ALAC)")
    if output_format == "aiff":
        if ext in AIFF_EXT and is_cdj_safe_aiff(
            path, bit_depth=bits, sample_rate=rate
        ):
            return "copy", True, bits, rate
        codec = pcm_codec_for_depth(bits, output_format="aiff")
        return codec, False, bits, rate
    if ext in ALAC_EXT:
        codec = pcm_codec_for_depth(bits, output_format="wav")
        return codec, False, bits, rate
    if ext in WAV_EXT:
        if is_cdj_safe_wav(path, bit_depth=bits, sample_rate=rate):
            return "copy", True, bits, rate
        codec = pcm_codec_for_depth(bits, output_format="wav")
        return codec, False, bits, rate
    if ext in {".flac", ".aiff", ".aif"}:
        codec = pcm_codec_for_depth(bits, output_format="wav")
        return codec, False, bits, rate
    raise CliError(f"unsupported format: {path}")


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
                copy_wav=False,
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
        item.copy_wav = is_copy
        item.codec = None if is_copy else codec
        item.bit_depth = bits
        item.sample_rate = rate
        item.duration_seconds = parse_duration_seconds(probe)
        in_place = same_file(item.source_path, item.dest_path)
        if output_format == "aiff":
            if in_place:
                cover = cached_cover_jpeg(
                    item.source_path,
                    cover_cache,
                    lock=cover_lock,
                    cancel_event=cancel_event,
                )
                if _is_canonical_aiff_output(
                    item.dest_path,
                    item.source_el,
                    cover,
                    bit_depth=bits,
                    sample_rate=rate,
                ):
                    item.noop = True
                else:
                    _record_error(
                        "refusing to convert in place "
                        f"(source is not a canonical AIFF output): "
                        f"{item.source_path}"
                    )
            else:
                item.noop = False
        else:
            item.noop = is_copy and in_place
            if (not is_copy) and in_place:
                _record_error(
                    "refusing to convert in place "
                    f"(source is not CDJ-safe WAV): {item.source_path}"
                )
        _finish_progress(item)

    if unique:
        workers = convert_worker_count(len(unique))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(probe_one, item) for item in unique]
            for fut in as_completed(futures):
                fut.result()
                if cancel_event is not None and cancel_event.is_set():
                    break
        finally:
            cancelled = cancel_event is not None and cancel_event.is_set()
            pool.shutdown(wait=not cancelled, cancel_futures=cancelled)
        if cancel_event is not None and cancel_event.is_set():
            return None, []

    suffix = AIFF_SUFFIX if output_format == "aiff" else WAV_SUFFIX
    wav_playlist_name = f"{playlist_name}{suffix}"
    plan = Plan(
        playlist_name=playlist_name,
        wav_playlist_name=wav_playlist_name,
        wav_dir=wav_dir_abs,
        playlist_dir=format_media_dir(wav_dir_abs, output_format),
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

