#!/usr/bin/env python3
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import rb_playlist_to_wav as rb


def write_pcm_wav(
    path: Path,
    *,
    sample_rate: int = 44100,
    channels: int = 2,
    bits: int = 16,
    format_tag: int = 1,
    fmt_extra: bytes = b"",
    extra_chunks: list[tuple[bytes, bytes]] | None = None,
    frames: int = 8,
) -> None:
    """Write a minimal little-endian PCM/extensible WAV for header tests."""
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    data = b"\x00" * (frames * block_align)
    fmt_core = struct.pack(
        "<HHIIHH",
        format_tag,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
    )
    fmt_payload = fmt_core + fmt_extra
    chunks: list[tuple[bytes, bytes]] = [(b"fmt ", fmt_payload)]
    if extra_chunks:
        chunks.extend(extra_chunks)
    chunks.append((b"data", data))
    body = b""
    for cid, payload in chunks:
        body += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            body += b"\x00"
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)


class WavHeaderParseTests(unittest.TestCase):
    def test_parse_simple_pcm_fmt_and_chunk_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.wav"
            write_pcm_wav(path)
            info = rb.parse_wav_info(path)
            self.assertEqual(info.format_tag, 1)
            self.assertEqual(info.channels, 2)
            self.assertEqual(info.sample_rate, 44100)
            self.assertEqual(info.bits_per_sample, 16)
            self.assertEqual(info.fmt_chunk_size, 16)
            self.assertEqual(info.chunk_ids, ("fmt ", "data"))

    def test_parse_extensible_fmt_and_list_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ext.wav"
            # WAVE_FORMAT_EXTENSIBLE: cbSize=22 + GUID (typical 40-byte fmt)
            extra = struct.pack("<H", 22) + bytes(22)
            write_pcm_wav(
                path,
                sample_rate=48000,
                bits=24,
                format_tag=0xFFFE,
                fmt_extra=extra,
                extra_chunks=[(b"LIST", b"INFO" + b"\x00" * 4)],
            )
            info = rb.parse_wav_info(path)
            self.assertEqual(info.format_tag, 0xFFFE)
            self.assertEqual(info.channels, 2)
            self.assertEqual(info.sample_rate, 48000)
            self.assertEqual(info.bits_per_sample, 24)
            self.assertEqual(info.fmt_chunk_size, 40)
            self.assertEqual(info.chunk_ids, ("fmt ", "LIST", "data"))


class CdjSafeWavTests(unittest.TestCase):
    def test_safe_16bit_stereo_44100_pcm_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "safe.wav"
            write_pcm_wav(path)
            self.assertTrue(rb.is_cdj_safe_wav(path))

    def test_rejects_extensible_list_wrong_rate_depth_and_mono(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                ("list.wav", dict(extra_chunks=[(b"LIST", b"INFO\x00\x00\x00\x00")])),
                (
                    "ext.wav",
                    dict(
                        bits=24,
                        format_tag=0xFFFE,
                        fmt_extra=struct.pack("<H", 22) + bytes(22),
                    ),
                ),
                ("rate.wav", dict(sample_rate=48000)),
                ("depth.wav", dict(bits=24)),
                ("mono.wav", dict(channels=1)),
            ]
            for name, kwargs in cases:
                path = root / name
                write_pcm_wav(path, **kwargs)
                self.assertFalse(rb.is_cdj_safe_wav(path), msg=name)

    def test_missing_file_is_not_safe(self) -> None:
        self.assertFalse(rb.is_cdj_safe_wav(Path("/no/such/file.wav")))


class CdjSafeConvertTests(unittest.TestCase):
    def test_ffmpeg_converts_extensible_48k24_to_cdj_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.wav"
            dest = root / "out.wav"
            # Real FFmpeg 24-bit output is WAVE_FORMAT_EXTENSIBLE + 48 kHz.
            proc = __import__("subprocess").run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=f=440:d=0.2",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s24le",
                    str(src),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            info = rb.parse_wav_info(src)
            self.assertEqual(info.format_tag, 0xFFFE)
            self.assertEqual(info.sample_rate, 48000)
            self.assertFalse(rb.is_cdj_safe_wav(src))

            rb.run_ffmpeg(src, dest, "pcm_s16le", force=True)

            self.assertTrue(rb.is_cdj_safe_wav(dest))
            out = rb.parse_wav_info(dest)
            self.assertEqual(out.format_tag, 1)
            self.assertEqual(out.sample_rate, 44100)
            self.assertEqual(out.bits_per_sample, 16)
            self.assertEqual(out.channels, 2)
            self.assertEqual(out.chunk_ids, ("fmt ", "data"))


class ClassifyCdjSafeTests(unittest.TestCase):
    def test_safe_wav_is_copy_unsafe_and_flac_convert_to_s16(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            safe = root / "safe.wav"
            write_pcm_wav(safe)
            unsafe = root / "unsafe.wav"
            write_pcm_wav(unsafe, sample_rate=48000)
            flac = root / "track.flac"
            flac.write_bytes(b"fLaC")

            codec, is_copy = rb.classify_source(
                safe, {"codec_name": "pcm_s16le", "sample_fmt": "s16"}
            )
            self.assertTrue(is_copy)
            self.assertEqual(codec, "copy")

            codec, is_copy = rb.classify_source(
                unsafe, {"codec_name": "pcm_s16le", "sample_fmt": "s16"}
            )
            self.assertFalse(is_copy)
            self.assertEqual(codec, "pcm_s16le")

            codec, is_copy = rb.classify_source(
                flac,
                {
                    "codec_name": "flac",
                    "sample_fmt": "s32",
                    "bits_per_raw_sample": "24",
                },
            )
            self.assertFalse(is_copy)
            self.assertEqual(codec, "pcm_s16le")


class SkipCdjSafeDestTests(unittest.TestCase):
    def test_skips_cdj_safe_dest_reconverts_unsafe_unless_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            playlist_dir = root / "WAV" / "P"
            playlist_dir.mkdir(parents=True)
            safe_dest = playlist_dir / "safe.wav"
            write_pcm_wav(safe_dest)
            unsafe_dest = playlist_dir / "unsafe.wav"
            write_pcm_wav(unsafe_dest, sample_rate=48000)

            def make_item(dest: Path) -> rb.PlannedTrack:
                el = ET.Element(
                    "TRACK", {"TrackID": "1", "Location": rb.encode_location(src)}
                )
                return rb.PlannedTrack(
                    source_el=el,
                    source_path=src,
                    dest_path=dest,
                    dest_location=rb.encode_location(dest),
                    dest_name=dest.name,
                    codec="pcm_s16le",
                    copy_wav=False,
                    noop=False,
                )

            safe_item = make_item(safe_dest)
            unsafe_item = make_item(unsafe_dest)
            plan = rb.Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                wav_dir=root / "WAV",
                playlist_dir=playlist_dir,
                output=root / "o.xml",
                tracks=[safe_item, unsafe_item],
                unique=[safe_item, unsafe_item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
            )
            converted: list[Path] = []

            def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool) -> None:
                converted.append(dest)
                write_pcm_wav(dest)

            with mock.patch.object(rb, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = rb.convert_unique(plan, force=False)
            self.assertEqual(stats.skipped, 1)
            self.assertEqual(stats.converted, 1)
            self.assertEqual(converted, [unsafe_dest])

            converted.clear()
            with mock.patch.object(rb, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = rb.convert_unique(plan, force=True)
            self.assertEqual(stats.converted, 2)
            self.assertEqual(converted, [safe_dest, unsafe_dest])


class NoopUnsafeInPlaceTests(unittest.TestCase):
    def test_prepare_errors_when_unsafe_wav_source_equals_dest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav_dir = root / "out"
            playlist = "Set"
            playlist_dir = wav_dir / playlist
            playlist_dir.mkdir(parents=True)
            # Dest path is wav_dir/playlist/stem.wav — put unsafe source there.
            src = playlist_dir / "track.wav"
            write_pcm_wav(src, sample_rate=48000)
            xml_path = root / "c.xml"
            xml_path.write_text(
                f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="t" Location="{rb.encode_location(src)}" Kind="WAV File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="{playlist}" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
""",
                encoding="utf-8",
            )
            with mock.patch.object(rb, "require_tools", return_value=[]):
                _, errors = rb.prepare(
                    xml_path, playlist, wav_dir, root / "import.xml"
                )
            self.assertTrue(
                any("refusing to convert in place" in e for e in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
