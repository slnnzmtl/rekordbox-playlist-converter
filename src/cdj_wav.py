"""CDJ-safe WAV parse, profile validation, and PCM rewrite (fmt+data only)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import iff_chunks
from cli_error import CliError

CDJ_SAFE_CHANNELS = 2
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
    declared = struct.unpack_from("<I", data, 4)[0]
    if declared + 8 != len(data):
        raise CliError(f"invalid RIFF length in {path}")
    chunk_ids: list[str] = []
    format_tag = channels = sample_rate = bits_per_sample = fmt_chunk_size = 0
    found_fmt = False
    offset = 12
    try:
        for cid, size, payload_start in iff_chunks.iter_chunks(
            data, endian="little", start=12
        ):
            chunk_ids.append(cid.decode("ascii", errors="replace"))
            if cid == b"fmt ":
                if size < 16:
                    raise CliError(f"fmt chunk too small in {path}")
                format_tag, channels, sample_rate, _byte_rate, _align, bits_per_sample = (
                    struct.unpack_from("<HHIIHH", data, payload_start)
                )
                fmt_chunk_size = size
                found_fmt = True
            offset = payload_start + size + (size % 2)
    except ValueError as exc:
        raise CliError(f"truncated WAV chunk in {path}: {exc}") from exc
    if offset != len(data):
        raise CliError(f"trailing bytes after WAV chunks in {path}")
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


def is_cdj_safe_wav(
    path: Path,
    *,
    bit_depth: int = 24,
    sample_rate: int = 48000,
) -> bool:
    """True if path is stereo WAVE_FORMAT_PCM with fmt+data only at the given quality."""
    if bit_depth not in (16, 24):
        bit_depth = 24
    if sample_rate not in (44100, 48000):
        sample_rate = 48000
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
        and info.sample_rate == sample_rate
        and info.bits_per_sample == bit_depth
        and info.chunk_ids == CDJ_SAFE_CHUNK_IDS
    )


def _rewrite_wav_pcm(source: Path, dest: Path) -> None:
    """Rewrite as WAVE_FORMAT_PCM with only fmt + data (never EXTENSIBLE)."""
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read WAV: {source}: {exc}") from exc
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise CliError(f"not a RIFF/WAVE file: {source}")
    fmt_payload: bytes | None = None
    pcm_data: bytes | None = None
    try:
        for cid, size, payload_start in iff_chunks.iter_chunks(
            data, endian="little", start=12
        ):
            payload_end = payload_start + size
            if cid == b"fmt ":
                if size < 16:
                    raise CliError(f"fmt chunk too small in {source}")
                format_tag, channels, sample_rate, _br, _ba, bits = struct.unpack_from(
                    "<HHIIHH", data, payload_start
                )
                if format_tag not in (WAVE_FORMAT_PCM, 0xFFFE):
                    raise CliError(f"unsupported WAV format tag in {source}")
                if channels != CDJ_SAFE_CHANNELS:
                    raise CliError(f"WAV must be stereo: {source}")
                if bits not in (16, 24):
                    raise CliError(f"unsupported bit depth in {source}")
                if sample_rate not in (44100, 48000):
                    raise CliError(f"unsupported sample rate in {source}")
                block_align = channels * (bits // 8)
                byte_rate = sample_rate * block_align
                fmt_payload = struct.pack(
                    "<HHIIHH",
                    WAVE_FORMAT_PCM,
                    channels,
                    sample_rate,
                    byte_rate,
                    block_align,
                    bits,
                )
            elif cid == b"data":
                pcm_data = data[payload_start:payload_end]
    except ValueError as exc:
        raise CliError(f"truncated WAV chunk in {source}: {exc}") from exc
    if fmt_payload is None or pcm_data is None:
        raise CliError(f"WAV missing fmt or data: {source}")
    body = (
        b"fmt "
        + struct.pack("<I", len(fmt_payload))
        + fmt_payload
        + b"data"
        + struct.pack("<I", len(pcm_data))
        + pcm_data
    )
    if len(pcm_data) % 2:
        body += b"\x00"
    dest.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
