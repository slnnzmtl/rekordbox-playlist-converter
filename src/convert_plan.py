"""Plan and convert unique tracks (WAV/AIFF); no XML write or CLI wizard."""

from __future__ import annotations

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
from pathlib import Path
from typing import Callable

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
    """Worker count from cpu_count, capped at 4 and CONVERT_WORKERS_MAX."""
    n = min(4, max(1, os.cpu_count() or 4))
    return max(CONVERT_WORKERS_MIN, min(n, CONVERT_WORKERS_MAX))


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


def convert_unique(
    plan: Plan,
    force: bool,
    *,
    progress: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> ConvertStats:
    stats = ConvertStats()
    plan.playlist_dir.mkdir(parents=True, exist_ok=True)
    items = plan.unique
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

    def process_one(item: PlannedTrack) -> None:
        if cancel_event is not None and cancel_event.is_set():
            return
        name = item.dest_name
        is_aiff = item.dest_path.suffix.lower() == ".aiff"
        if item.noop:
            with stats_lock:
                stats.skipped += 1
            finish("skip", name)
            return
        try:
            if is_aiff:
                cover = cached_cover_jpeg(
                    item.source_path, plan.cover_cache, lock=cover_lock
                )
                if not force and _is_canonical_aiff_output(
                    item.dest_path,
                    item.source_el,
                    cover,
                    bit_depth=item.bit_depth,
                    sample_rate=item.sample_rate,
                ):
                    with stats_lock:
                        stats.skipped += 1
                    finish("skip", name)
                    return
                action = "copy" if item.copy_wav else "convert"
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
                finish(action, name)
                return
            if not force and is_cdj_safe_wav(
                item.dest_path,
                bit_depth=item.bit_depth,
                sample_rate=item.sample_rate,
            ):
                with stats_lock:
                    stats.skipped += 1
                finish("skip", name)
                return
            if item.copy_wav:
                _copy_wav_atomic(
                    item.source_path, item.dest_path, cancel_event=cancel_event
                )
                with stats_lock:
                    stats.copied += 1
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
            finish("convert", name)
        except CancelledError:
            try:
                item.dest_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        except Exception as exc:  # noqa: BLE001 — collect all; report after pool
            with stats_lock:
                stats.errors.append(str(exc))
            try:
                item.dest_path.unlink(missing_ok=True)
            except OSError:
                pass
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


def abs_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path

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

    unique: list[PlannedTrack] = []
    unique_dest: dict[str, PlannedTrack] = {}
    for item in planned:
        dest_key = collision_key(item.dest_name)
        if dest_key in unique_dest:
            kept = unique_dest[dest_key]
            if item.source_path != kept.source_path:
                warnings.append(
                    f"skipped filename collision: {item.source_path} → {kept.dest_name}"
                )
            continue
        unique_dest[dest_key] = item
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
        playlist_dir=playlist_dir,
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
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with open(source, "rb") as src_f, open(tmp, "wb") as dst_f:
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
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
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
    if is_aiff:
        fd, tmp_name = tempfile.mkstemp(
            dir=dest.parent, prefix=".aiff-", suffix=".tmp.aiff"
        )
    else:
        fd, tmp_name = tempfile.mkstemp(
            dir=dest.parent, prefix=".wav-", suffix=".tmp.wav"
        )
    os.close(fd)
    out_tmp = Path(tmp_name)
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
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
                raise CancelledError(f"conversion cancelled for {source}")
            if time.monotonic() >= deadline:
                _kill_ffmpeg_drain(proc)
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
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
                # Muxer may leave extras; normalize then re-check.
                fd2, norm_name = tempfile.mkstemp(
                    dir=dest.parent, prefix=".aiff-ff-", suffix=".tmp.aiff"
                )
                os.close(fd2)
                norm = Path(norm_name)
                try:
                    _normalize_aiff_audio_chunks(out_tmp, norm)
                    os.replace(norm, out_tmp)
                except Exception:
                    try:
                        norm.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
                if not is_cdj_safe_aiff(out_tmp, bit_depth=depth, sample_rate=rate):
                    raise CliError(
                        f"ffmpeg produced a non-CDJ-safe AIFF for {source}: {dest}"
                    )
        else:
            if not is_cdj_safe_wav(out_tmp, bit_depth=depth, sample_rate=rate):
                fd2, rew_name = tempfile.mkstemp(
                    dir=dest.parent, prefix=".wav-ff-", suffix=".tmp.wav"
                )
                os.close(fd2)
                rew = Path(rew_name)
                try:
                    _rewrite_wav_pcm(out_tmp, rew)
                    os.replace(rew, out_tmp)
                except Exception:
                    try:
                        rew.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
                if not is_cdj_safe_wav(out_tmp, bit_depth=depth, sample_rate=rate):
                    raise CliError(
                        f"ffmpeg produced a non-CDJ-safe WAV for {source}: {dest}"
                    )
        os.replace(out_tmp, dest)
    except Exception:
        try:
            out_tmp.unlink(missing_ok=True)
        except OSError:
            pass
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
            # run_ffmpeg already normalized when needed; skip a second pass.
        write_aiff_id3(tmp, source_el, cover)
        if not _is_canonical_aiff_output(
            tmp, source_el, cover, bit_depth=bit_depth, sample_rate=sample_rate
        ):
            raise CliError(f"AIFF failed canonical validation for {source}")
        os.replace(tmp, dest)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


