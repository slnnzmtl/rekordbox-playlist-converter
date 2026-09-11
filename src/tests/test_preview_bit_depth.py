#!/usr/bin/env python3
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from preview_bit_depth import read_preview_bit_depth


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _alac_m4a_with_bit_depth(bits: int) -> bytes:
    """Minimal moov/stsd ALAC sample entry with ALACSpecificConfig.bitDepth."""
    config = struct.pack(
        ">IBBBBBBHIII",
        4096,  # frameLength
        0,  # compatibleVersion
        bits,  # bitDepth
        40,  # pb
        10,  # mb
        14,  # kb
        2,  # numChannels
        255,  # maxRun
        0,  # maxFrameBytes
        0,  # avgBitRate
        44100,  # sampleRate
    )
    specific = _box(b"alac", config)
    # AudioSampleEntry: 6 reserved + data_ref + QT sound fields (20 bytes).
    sample_entry = (
        bytes(6)
        + struct.pack(">H", 1)
        + bytes(8)
        + struct.pack(">HH", 2, 16)
        + bytes(4)
        + struct.pack(">I", 44100 << 16)
        + specific
    )
    alac_entry = _box(b"alac", sample_entry)
    stsd = _box(b"stsd", struct.pack(">II", 0, 1) + alac_entry)
    stbl = _box(b"stbl", stsd)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", minf)
    trak = _box(b"trak", mdia)
    moov = _box(b"moov", trak)
    ftyp = _box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A ")
    return ftyp + moov


def _flac_with_bit_depth(bits: int) -> bytes:
    """Minimal fLaC + STREAMINFO (type 0). Bit depth is (bits-1) in 5 bits."""
    min_block = struct.pack(">H", 4096)
    max_block = struct.pack(">H", 4096)
    min_frame = b"\x00\x00\x12"
    max_frame = b"\x00\x00\x34"
    rate = 44100
    channels_minus_1 = 1
    bits_minus_1 = bits - 1
    total_samples = 0
    packed = (
        (rate << 44) | (channels_minus_1 << 41) | (bits_minus_1 << 36) | total_samples
    )
    body = min_block + max_block + min_frame + max_frame + packed.to_bytes(8, "big")
    body += bytes(16)  # MD5
    last_and_type = 0x80  # last block, type STREAMINFO
    header = bytes([last_and_type, 0x00, 0x00, len(body)])
    return b"fLaC" + header + body


def _wav_chunk(cid: bytes, payload: bytes) -> bytes:
    return cid + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


def _wav_with_bit_depth(bits: int, *, junk_before_fmt: bool = False) -> bytes:
    """Minimal PCM WAV; bits_per_sample lives at fmt payload offset 14."""
    channels = 2
    rate = 44100
    block_align = channels * (bits // 8)
    byte_rate = rate * block_align
    fmt = struct.pack(
        "<HHIIHH",
        1,  # WAVE_FORMAT_PCM
        channels,
        rate,
        byte_rate,
        block_align,
        bits,
    )
    data = bytes(block_align * 4)
    chunks = b""
    if junk_before_fmt:
        chunks += _wav_chunk(b"JUNK", bytes(12))
    chunks += _wav_chunk(b"fmt ", fmt)
    chunks += _wav_chunk(b"data", data)
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def _aiff_with_bit_depth(bits: int) -> bytes:
    """Minimal FORM/AIFF with COMM bits after channels + frames."""
    channels = 2
    frames = 4
    # 80-bit IEEE extended for 44100 (same encoding as cdj_aiff helpers).
    rate_bytes = bytes.fromhex("400eac44000000000000")
    comm = struct.pack(">hIh", channels, frames, bits) + rate_bytes
    ssnd = struct.pack(">II", 0, 0) + bytes(channels * frames * (bits // 8))
    body = (
        b"COMM"
        + struct.pack(">I", len(comm))
        + comm
        + b"SSND"
        + struct.pack(">I", len(ssnd))
        + ssnd
    )
    return b"FORM" + struct.pack(">I", 4 + len(body)) + b"AIFF" + body


class PreviewBitDepthTests(unittest.TestCase):
    def test_wav_fmt_reports_bits_per_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.wav"
            path.write_bytes(_wav_with_bit_depth(24, junk_before_fmt=True))
            self.assertEqual(read_preview_bit_depth(path), 24)

    def test_aiff_comm_reports_bits_per_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.aiff"
            path.write_bytes(_aiff_with_bit_depth(24))
            self.assertEqual(read_preview_bit_depth(path), 24)

    def test_flac_streaminfo_reports_bits_per_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.flac"
            path.write_bytes(_flac_with_bit_depth(24))
            self.assertEqual(read_preview_bit_depth(path), 24)

    def test_alac_mp4_config_reports_bit_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.m4a"
            path.write_bytes(_alac_m4a_with_bit_depth(24))
            self.assertEqual(read_preview_bit_depth(path), 24)

    def test_alac_finds_moov_after_mdat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "late-moov.m4a"
            full = _alac_m4a_with_bit_depth(24)
            ftyp_size = struct.unpack(">I", full[:4])[0]
            ftyp, moov = full[:ftyp_size], full[ftyp_size:]
            mdat = struct.pack(">I", 8 + 80_000) + b"mdat" + bytes(80_000)
            path.write_bytes(ftyp + mdat + moov)
            self.assertEqual(read_preview_bit_depth(path), 24)

    def test_missing_or_unknown_file_returns_none(self) -> None:
        missing = Path("/no/such/preview-bit-depth.flac")
        self.assertIsNone(read_preview_bit_depth(missing))
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "track.wav"
            junk.write_bytes(b"RIFF")
            self.assertIsNone(read_preview_bit_depth(junk))


if __name__ == "__main__":
    unittest.main()
