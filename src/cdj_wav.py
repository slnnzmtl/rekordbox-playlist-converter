"""CDJ-safe WAV parse and validation (16-bit / 44.1 kHz / stereo PCM)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from cli_error import CliError

CDJ_SAFE_SAMPLE_RATE = 44100
CDJ_SAFE_CHANNELS = 2
CDJ_SAFE_BIT_DEPTH = 16
WAVE_FORMAT_PCM = 1
CDJ_SAFE_CHUNK_IDS = ("fmt ", "data")


@dataclass(frozen=True)
class WavInfo:
    format_tag: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    fmt_chunk_size: int
    chunk_ids: tuple[str, ...]


def parse_wav_info(path: Path) -> WavInfo:
    """Parse RIFF/WAVE chunk layout and the primary fmt fields."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read WAV: {path}: {exc}") from exc
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise CliError(f"not a RIFF/WAVE file: {path}")
    chunk_ids: list[str] = []
    format_tag = channels = sample_rate = bits_per_sample = fmt_chunk_size = 0
    found_fmt = False
    offset = 12
    while offset + 8 <= len(data):
        cid = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        if payload_end > len(data):
            raise CliError(f"truncated WAV chunk {cid!r} in {path}")
        chunk_ids.append(cid.decode("ascii", errors="replace"))
        if cid == b"fmt ":
            if size < 16:
                raise CliError(f"fmt chunk too small in {path}")
            format_tag, channels, sample_rate, _byte_rate, _align, bits_per_sample = (
                struct.unpack_from("<HHIIHH", data, payload_start)
            )
            fmt_chunk_size = size
            found_fmt = True
        offset = payload_end + (size % 2)
    if not found_fmt:
        raise CliError(f"WAV missing fmt chunk: {path}")
    return WavInfo(
        format_tag=format_tag,
        channels=channels,
        sample_rate=sample_rate,
        bits_per_sample=bits_per_sample,
        fmt_chunk_size=fmt_chunk_size,
        chunk_ids=tuple(chunk_ids),
    )


def is_cdj_safe_wav(path: Path) -> bool:
    """True if path is 16-bit / 44.1 kHz / stereo WAVE_FORMAT_PCM with fmt+data only."""
    if not path.is_file():
        return False
    try:
        info = parse_wav_info(path)
    except CliError:
        return False
    return (
        info.format_tag == WAVE_FORMAT_PCM
        and info.fmt_chunk_size == 16
        and info.channels == CDJ_SAFE_CHANNELS
        and info.sample_rate == CDJ_SAFE_SAMPLE_RATE
        and info.bits_per_sample == CDJ_SAFE_BIT_DEPTH
        and info.chunk_ids == CDJ_SAFE_CHUNK_IDS
    )
