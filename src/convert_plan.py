"""Plan and convert unique tracks (WAV/AIFF); no XML write or CLI wizard."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

import converter_manifest
import ffmpeg_tools
from cdj_aiff import (
    _is_canonical_aiff_output,
    _normalize_aiff_audio_chunks,
    extract_cover_jpeg,
    is_cdj_safe_aiff,
    write_aiff_id3,
)
from cdj_wav import (
    CDJ_SAFE_CHANNELS,
    _rewrite_wav_pcm,
    is_cdj_safe_wav,
)
from cli_error import CancelledError, CliError
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
        self._lock = threading.Lock()

    def update(self, current: int, action: str, name: str) -> None:
        with self._lock:
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
        with self._lock:
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
    bit_depth: int = 24
    sample_rate: int = 48000
    duration_seconds: float | None = None


@dataclass
class ConversionPreviewItem:
    relative_dest: str
    action: str
    bit_depth: int
    sample_rate: int
    size_bytes: int | None = None
    size_display: str = "—"


@dataclass
class ConversionPreview:
    selected: int
    resolved: int
    unique_outputs: int
    duplicates: int
    missing: int
    items: list[ConversionPreviewItem] = field(default_factory=list)


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
    output_format: str = "wav"
    max_bit_depth: int = 24
    max_sample_rate: int = 48000
    cover_cache: dict[Path, bytes | None] = field(default_factory=dict)


def cached_cover_jpeg(
    source: Path,
    cache: dict[Path, bytes | None],
    *,
    lock: threading.Lock | None = None,
) -> bytes | None:
    """Extract cover once per source path for the duration of a convert run."""
    if lock is None:
        if source not in cache:
            cache[source] = extract_cover_jpeg(source)
        return cache[source]
    with lock:
        if source not in cache:
            cache[source] = extract_cover_jpeg(source)
        return cache[source]


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
) -> str:
    """Classify read-only action: reuse, copy, or transcode."""
    if item.noop:
        return "reuse"
    is_aiff = item.dest_path.suffix.lower() == ".aiff"
    if not force:
        if is_aiff:
            cover = cached_cover_jpeg(
                item.source_path, plan.cover_cache, lock=cover_lock
            )
            if _is_canonical_aiff_output(
                item.dest_path,
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


def _plan_for_preview_item(plans: list[Plan], item: PlannedTrack) -> Plan:
    for plan in plans:
        if item in plan.unique or item in plan.tracks:
            return plan
    return plans[0]


def _preview_size(item: PlannedTrack, action: str) -> tuple[int | None, str]:
    """Return (bytes, display). Reuse uses dest size; else estimate PCM."""
    if action == "reuse":
        try:
            if item.dest_path.is_file():
                size = item.dest_path.stat().st_size
                return size, str(size)
        except OSError:
            pass
        return None, "—"
    duration = item.duration_seconds
    if duration is None or not math.isfinite(duration) or duration < 0:
        return None, "—"
    bytes_per_sample = 2 if item.bit_depth == 16 else 3
    estimated = int(duration * item.sample_rate * bytes_per_sample * 2)
    return estimated, f"≈ {estimated}"


def build_conversion_preview(
    plans: list[Plan],
    items: list[PlannedTrack],
    force: bool,
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
    preview_items: list[ConversionPreviewItem] = []
    for item in items:
        plan = _plan_for_preview_item(plans, item)
        action = planned_action(plan, item, force)
        try:
            relative_dest = item.dest_path.relative_to(wav_dir).as_posix()
        except ValueError:
            relative_dest = item.dest_path.name
        size_bytes, size_display = _preview_size(item, action)
        preview_items.append(
            ConversionPreviewItem(
                relative_dest=relative_dest,
                action=action,
                bit_depth=item.bit_depth,
                sample_rate=item.sample_rate,
                size_bytes=size_bytes,
                size_display=size_display,
            )
        )
    return ConversionPreview(
        selected=selected,
        resolved=resolved,
        unique_outputs=unique_outputs,
        duplicates=duplicates,
        missing=missing,
        items=preview_items,
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
        fmt = "aiff" if item.dest_path.suffix.lower() == ".aiff" else "wav"
        with stats_lock:
            stats.succeeded.add((source_key(item.source_path), fmt))

    def process_one(item: PlannedTrack) -> None:
        if cancel_event is not None and cancel_event.is_set():
            return
        name = item.dest_name
        is_aiff = item.dest_path.suffix.lower() == ".aiff"
        action = planned_action(plan, item, force, cover_lock=cover_lock)
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


@dataclass
class ConvertStats:
    converted: int = 0
    copied: int = 0
    skipped: int = 0
    appended: int = 0
    errors: list[str] = field(default_factory=list)
    # (source_key, format) that skipped, copied, or converted successfully.
    succeeded: set[tuple[str, str]] = field(default_factory=set)


def abs_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


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
    if output_format == "wav":
        return "WAV"
    if output_format == "aiff":
        return "AIFF"
    raise CliError(f"unsupported output format: {output_format!r}")


def format_media_dir(wav_dir: Path, output_format: str) -> Path:
    """Return wav_dir/WAV or wav_dir/AIFF for audio output."""
    return wav_dir / format_dir_name(output_format)


def playlist_dir_name(playlist_name: str) -> str:
    """Filesystem-safe single directory component from the playlist name."""
    name = unicodedata.normalize("NFC", playlist_name)
    name = name.replace("/", "_").replace("\\", "_").replace("\0", "")
    name = name.rstrip(" .")
    if not name or name in {".", ".."}:
        raise CliError(f"playlist name is not usable as a directory: {playlist_name!r}")
    return name


collision_key = converter_manifest.collision_key


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
    if max_bit_depth not in (16, 24):
        max_bit_depth = 24
    if max_sample_rate not in (44100, 48000):
        max_sample_rate = 48000

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


def pcm_codec_for_depth(bit_depth: int, *, output_format: str = "wav") -> str:
    """Map effective bit depth to an ffmpeg PCM codec for the output container."""
    if output_format == "aiff":
        return "pcm_s16be" if bit_depth == 16 else "pcm_s24be"
    return "pcm_s16le" if bit_depth == 16 else "pcm_s24le"


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
    if output_format == "aiff":
        if ext in AIFF_EXT and is_cdj_safe_aiff(
            path, bit_depth=bits, sample_rate=rate
        ):
            return "copy", True, bits, rate
        if ext in ALAC_EXT and codec_name != "alac":
            raise CliError(f"unsupported format: {path} (expected ALAC)")
        codec = pcm_codec_for_depth(bits, output_format="aiff")
        return codec, False, bits, rate
    if ext in ALAC_EXT:
        if codec_name != "alac":
            raise CliError(f"unsupported format: {path} (expected ALAC)")
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


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


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
    if output_format not in ("wav", "aiff"):
        output_format = "wav"
    if max_bit_depth not in (16, 24):
        max_bit_depth = 24
    if max_sample_rate not in (44100, 48000):
        max_sample_rate = 48000
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
            probe = ffmpeg_tools.run_ffprobe(item.source_path)
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
                    item.source_path, cover_cache, lock=cover_lock
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
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(probe_one, item) for item in unique]
            for fut in as_completed(futures):
                fut.result()
                if cancel_event is not None and cancel_event.is_set():
                    break
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


def _kill_ffmpeg_drain(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.wait()
    finally:
        proc.communicate()


_COPY_CHUNK_SIZE = 1024 * 1024


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _temp_beside(dest: Path, *, aiff: bool) -> Path:
    kind = "aiff" if aiff else "wav"
    fd, name = tempfile.mkstemp(
        dir=dest.parent, prefix=f".{kind}-", suffix=f".tmp.{kind}"
    )
    os.close(fd)
    return Path(name)


def _rewrite_sidecar(path: Path, rewrite) -> None:
    sidecar = path.with_name(path.name + "~")
    try:
        rewrite(path, sidecar)
        os.replace(sidecar, path)
    except Exception:
        _unlink_quiet(sidecar)
        raise


def _copy_wav_atomic(
    source: Path,
    dest: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    """Copy WAV to dest via temp + os.replace; poll cancel between chunks."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=dest.parent, prefix=".wav-", suffix=".tmp.wav"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as dst_f, open(source, "rb") as src_f:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledError(f"conversion cancelled for {source}")
                chunk = src_f.read(_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                dst_f.write(chunk)
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError(f"conversion cancelled for {source}")
        os.replace(tmp, dest)
    except Exception:
        _unlink_quiet(tmp)
        raise


def run_ffmpeg(
    source: Path,
    dest: Path,
    codec: str,
    force: bool,
    *,
    sample_rate: int | None = None,
    bit_depth: int | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Encode dest as PCM WAV or AIFF at the planned depth/rate (no ID3)."""
    exe = ffmpeg_tools.tool_path("ffmpeg")
    if exe is None:
        raise CliError(
            "ffmpeg not found on PATH (install with: brew install ffmpeg)"
        )
    is_aiff = dest.suffix.lower() == ".aiff"
    rate = sample_rate if sample_rate in (44100, 48000) else 44100
    if bit_depth in (16, 24):
        depth = bit_depth
    elif codec in ("pcm_s16le", "pcm_s16be", "pcm_s24le", "pcm_s24be"):
        depth = ffmpeg_tools.bit_depth_of_codec(codec)
    else:
        depth = 16
    if is_aiff:
        audio_codec = (
            codec
            if codec in ("pcm_s16be", "pcm_s24be")
            else pcm_codec_for_depth(depth, output_format="aiff")
        )
    else:
        audio_codec = (
            codec
            if codec in ("pcm_s16le", "pcm_s24le")
            else pcm_codec_for_depth(depth, output_format="wav")
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    out_tmp = _temp_beside(dest, aiff=is_aiff)
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
    if ffmpeg_tools.ffmpeg_supports_soxr():
        cmd.extend(["-af", "aresample=resampler=soxr"])
    cmd.extend(
        [
            "-ar",
            str(rate),
            "-ac",
            str(CDJ_SAFE_CHANNELS),
            "-c:a",
            audio_codec,
            "-nostats",
            "-loglevel",
            "error",
            str(out_tmp),
        ]
    )
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CliError(
                "ffmpeg not found on PATH (install with: brew install ffmpeg)"
            ) from exc
        timeout_s = ffmpeg_tools.FFMPEG_CONVERT_TIMEOUT_S * CONVERT_WORKERS
        deadline = time.monotonic() + timeout_s
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                _kill_ffmpeg_drain(proc)
                raise CancelledError(f"conversion cancelled for {source}")
            if time.monotonic() >= deadline:
                _kill_ffmpeg_drain(proc)
                raise CliError(
                    f"ffmpeg timed out after {timeout_s}s for {source}"
                )
            time.sleep(0.05)
        _stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            err = (stderr or _stdout or "").strip() or f"exit {proc.returncode}"
            raise CliError(f"ffmpeg conversion failed for {source}: {err}")
        if is_aiff:
            if not is_cdj_safe_aiff(out_tmp, bit_depth=depth, sample_rate=rate):
                _rewrite_sidecar(out_tmp, _normalize_aiff_audio_chunks)
                if not is_cdj_safe_aiff(out_tmp, bit_depth=depth, sample_rate=rate):
                    raise CliError(
                        f"ffmpeg produced a non-CDJ-safe AIFF for {source}: {dest}"
                    )
        elif not is_cdj_safe_wav(out_tmp, bit_depth=depth, sample_rate=rate):
            _rewrite_sidecar(out_tmp, _rewrite_wav_pcm)
            if not is_cdj_safe_wav(out_tmp, bit_depth=depth, sample_rate=rate):
                raise CliError(
                    f"ffmpeg produced a non-CDJ-safe WAV for {source}: {dest}"
                )
        os.replace(out_tmp, dest)
    except Exception:
        _unlink_quiet(out_tmp)
        raise


def write_aiff_output(
    source: Path,
    dest: Path,
    source_el: ET.Element,
    *,
    passthrough: bool,
    codec: str | None,
    bit_depth: int = 24,
    sample_rate: int = 48000,
    cover_cache: dict[Path, bytes | None] | None = None,
    cancel_event: threading.Event | None = None,
    cover_lock: threading.Lock | None = None,
) -> None:
    """Atomically write AIFF: PCM then ID3, validate, os.replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if cover_cache is None:
        cover = extract_cover_jpeg(source)
    else:
        cover = cached_cover_jpeg(source, cover_cache, lock=cover_lock)
    tmp = _temp_beside(dest, aiff=True)
    try:
        if passthrough:
            _normalize_aiff_audio_chunks(source, tmp)
        else:
            if not codec:
                raise CliError(f"no codec planned for {source}")
            run_ffmpeg(
                source,
                tmp,
                codec,
                force=True,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                cancel_event=cancel_event,
            )
            if not is_cdj_safe_aiff(tmp, bit_depth=bit_depth, sample_rate=sample_rate):
                raise CliError(f"AIFF audio stage failed for {source}")
        write_aiff_id3(tmp, source_el, cover)
        if not _is_canonical_aiff_output(
            tmp, source_el, cover, bit_depth=bit_depth, sample_rate=sample_rate
        ):
            raise CliError(f"AIFF failed canonical validation for {source}")
        os.replace(tmp, dest)
    except Exception:
        _unlink_quiet(tmp)
        raise


