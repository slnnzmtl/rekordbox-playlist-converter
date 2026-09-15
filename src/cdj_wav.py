"""CDJ-safe WAV parse, profile validation, and PCM rewrite (fmt+data only)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import iff_chunks
from cli_error import CliError
from convert.quality import coerce_bit_depth, coerce_sample_rate

CDJ_SAFE_CHANNELS = 2
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
CDJ_SAFE_CHUNK_IDS = ("fmt ", "data")
# KSDATAFORMAT_SUBTYPE_PCM {00000001-0000-0010-8000-00aa00389b71}
KSDATAFORMAT_SUBTYPE_PCM = bytes.fromhex("0100000000001000800000aa00389b71")


@dataclass(frozen=True)
class WavInfo:
    format_tag: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    fmt_chunk_size: int
    chunk_ids: tuple[str, ...]
    valid_bits_per_sample: int | None = None
    sub_format: bytes | None = None


def parse_wav_info(path: Path) -> WavInfo:
    """Parse RIFF/WAVE chunk layout and the primary fmt fields.

    Streams chunk headers (and the fmt payload) instead of loading the whole file.
    """
    try:
        with path.open("rb") as fp:
            header = fp.read(12)
            if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
                raise CliError(f"not a RIFF/WAVE file: {path}")
            declared = struct.unpack_from("<I", header, 4)[0]
            fp.seek(0, 2)
            file_len = fp.tell()
            if declared + 8 != file_len:
                raise CliError(f"invalid RIFF length in {path}")
            chunk_ids: list[str] = []
            format_tag = channels = sample_rate = bits_per_sample = fmt_chunk_size = 0
            valid_bits_per_sample: int | None = None
            sub_format: bytes | None = None
            found_fmt = False
            offset = 12
            try:
                for cid, size, payload_start in iff_chunks.iter_chunks_file(
                    fp, endian="little", start=12, end=file_len
                ):
                    chunk_ids.append(cid.decode("ascii", errors="replace"))
                    if cid == b"fmt ":
                        if size < 16:
                            raise CliError(f"fmt chunk too small in {path}")
                        fp.seek(payload_start)
                        raw = fp.read(16)
                        if len(raw) < 16:
                            raise CliError(f"fmt chunk too small in {path}")
                        (
                            format_tag,
                            channels,
                            sample_rate,
                            _byte_rate,
                            _align,
                            bits_per_sample,
                        ) = struct.unpack("<HHIIHH", raw)
                        fmt_chunk_size = size
                        found_fmt = True
                        extra = b""
                        if size > 16:
                            extra = fp.read(size - 16)
                            if len(extra) < size - 16:
                                raise CliError(f"fmt chunk too small in {path}")
                        if (
                            format_tag == WAVE_FORMAT_EXTENSIBLE
                            and size >= 40
                            and len(extra) >= 24
                        ):
                            cb_size, valid_bits, _mask = struct.unpack_from(
                                "<HHI", extra, 0
                            )
                            if cb_size == 22:
                                valid_bits_per_sample = valid_bits
                                sub_format = extra[8:24]
                    offset = payload_start + size + (size % 2)
            except ValueError as exc:
                raise CliError(f"truncated WAV chunk in {path}: {exc}") from exc
            if offset != file_len:
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
                valid_bits_per_sample=valid_bits_per_sample,
                sub_format=sub_format,
            )
    except OSError as exc:
        raise CliError(f"cannot read WAV: {path}: {exc}") from exc


def is_cdj_safe_wav(
    path: Path,
    *,
    bit_depth: int = 24,
    sample_rate: int = 48000,
) -> bool:
    """True if path is stereo WAVE_FORMAT_PCM with fmt+data only at the given quality."""
    bit_depth = coerce_bit_depth(bit_depth)
    sample_rate = coerce_sample_rate(sample_rate)
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


def pcm_rewrite_supported(info: WavInfo) -> bool:
    """True when PCM samples can be copied into a CDJ-safe WAVE_FORMAT_PCM file."""
    if (
        info.channels != CDJ_SAFE_CHANNELS
        or info.bits_per_sample not in (16, 24)
        or info.sample_rate not in (44100, 48000)
    ):
        return False
    if info.format_tag == WAVE_FORMAT_PCM:
        return True
    return (
        info.format_tag == WAVE_FORMAT_EXTENSIBLE
        and info.fmt_chunk_size >= 40
        and info.sub_format == KSDATAFORMAT_SUBTYPE_PCM
        and info.valid_bits_per_sample == info.bits_per_sample
    )


def rewrite_wav_pcm(source: Path, dest: Path) -> None:
    """Rewrite as WAVE_FORMAT_PCM with only fmt + data (never EXTENSIBLE)."""
    info = parse_wav_info(source)
    if not pcm_rewrite_supported(info):
        raise CliError(f"unsupported WAV format tag in {source}")
    try:
        with source.open("rb") as fp:
            header = fp.read(12)
            if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
                raise CliError(f"not a RIFF/WAVE file: {source}")
            fp.seek(0, 2)
            end = fp.tell()
            fmt_payload: bytes | None = None
            data_start: int | None = None
            data_size: int | None = None
            try:
                for cid, size, payload_start in iff_chunks.iter_chunks_file(
                    fp, endian="little", start=12, end=end
                ):
                    if cid == b"fmt ":
                        block_align = info.channels * (info.bits_per_sample // 8)
                        byte_rate = info.sample_rate * block_align
                        fmt_payload = struct.pack(
                            "<HHIIHH",
                            WAVE_FORMAT_PCM,
                            info.channels,
                            info.sample_rate,
                            byte_rate,
                            block_align,
                            info.bits_per_sample,
                        )
                    elif cid == b"data":
                        data_start = payload_start
                        data_size = size
            except ValueError as exc:
                raise CliError(f"truncated WAV chunk in {source}: {exc}") from exc
            if fmt_payload is None or data_start is None or data_size is None:
                raise CliError(f"WAV missing fmt or data: {source}")
            pad = data_size % 2
            body_len = 8 + len(fmt_payload) + 8 + data_size + pad
            with dest.open("wb") as out:
                out.write(b"RIFF" + struct.pack("<I", 4 + body_len) + b"WAVE")
                out.write(b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload)
                out.write(b"data" + struct.pack("<I", data_size))
                iff_chunks.copy_file_range(fp, out, data_start, data_size)
                if pad:
                    out.write(b"\x00")
    except OSError as exc:
        raise CliError(f"cannot read WAV: {source}: {exc}") from exc
    except ValueError as exc:
        raise CliError(f"truncated WAV data while streaming copy") from exc

