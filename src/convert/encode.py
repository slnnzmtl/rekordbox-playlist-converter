"""Encode / copy helpers: ffmpeg, atomic WAV copy, AIFF write."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import ffmpeg_tools
from cdj_aiff import (
    extract_cover_jpeg,
    is_canonical_aiff_output,
    is_cdj_safe_aiff,
    normalize_aiff_audio_chunks,
    write_aiff_id3,
)
from cdj_wav import (
    CDJ_SAFE_CHANNELS,
    is_cdj_safe_wav,
    rewrite_wav_pcm,
)
from cli_error import CancelledError, CliError
from convert.format_policy import pcm_codec_for_depth
from convert.quality import coerce_output_format

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


def copy_wav_atomic(
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
    convert_workers: int = 1,
    output_format: str = "wav",
) -> None:
    """Encode dest as PCM WAV or AIFF at the planned depth/rate (no ID3)."""
    exe = ffmpeg_tools.tool_path("ffmpeg")
    if exe is None:
        raise CliError(
            "ffmpeg not found on PATH (install with: brew install ffmpeg)"
        )
    is_aiff = coerce_output_format(output_format) == "aiff"
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
        timeout_s = ffmpeg_tools.FFMPEG_CONVERT_TIMEOUT_S * max(1, int(convert_workers))
        try:
            _stdout, stderr = ffmpeg_tools.wait_proc(
                proc,
                cancel_event=cancel_event,
                timeout_s=timeout_s,
                cancel_message=f"conversion cancelled for {source}",
            )
        except subprocess.TimeoutExpired:
            raise CliError(
                f"ffmpeg timed out after {timeout_s}s for {source}"
            )
        if proc.returncode != 0:
            err = (stderr or _stdout or "").strip() or f"exit {proc.returncode}"
            raise CliError(f"ffmpeg conversion failed for {source}: {err}")
        if is_aiff:
            if not is_cdj_safe_aiff(out_tmp, bit_depth=depth, sample_rate=rate):
                _rewrite_sidecar(out_tmp, normalize_aiff_audio_chunks)
                if not is_cdj_safe_aiff(out_tmp, bit_depth=depth, sample_rate=rate):
                    raise CliError(
                        f"ffmpeg produced a non-CDJ-safe AIFF for {source}: {dest}"
                    )
        elif not is_cdj_safe_wav(out_tmp, bit_depth=depth, sample_rate=rate):
            _rewrite_sidecar(out_tmp, rewrite_wav_pcm)
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
    cover_lookup=None,
    convert_workers: int = 1,
) -> None:
    """Atomically write AIFF: PCM then ID3, validate, os.replace.

    When *cover_cache* is set, *cover_lookup* must be the shared cache helper
    (typically convert.plan.cached_cover_jpeg).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if cover_cache is None:
        cover = extract_cover_jpeg(source, cancel_event=cancel_event)
    else:
        if cover_lookup is None:
            raise CliError(f"cover_lookup required when cover_cache is set for {source}")
        cover = cover_lookup(
            source, cover_cache, lock=cover_lock, cancel_event=cancel_event
        )
    tmp = _temp_beside(dest, aiff=True)
    try:
        if passthrough:
            normalize_aiff_audio_chunks(source, tmp)
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
                convert_workers=convert_workers,
                output_format="aiff",
            )
            if not is_cdj_safe_aiff(tmp, bit_depth=bit_depth, sample_rate=sample_rate):
                raise CliError(f"AIFF audio stage failed for {source}")
        write_aiff_id3(tmp, source_el, cover)
        if not is_canonical_aiff_output(
            tmp, source_el, cover, bit_depth=bit_depth, sample_rate=sample_rate
        ):
            raise CliError(f"AIFF failed canonical validation for {source}")
        os.replace(tmp, dest)
    except Exception:
        _unlink_quiet(tmp)
        raise
