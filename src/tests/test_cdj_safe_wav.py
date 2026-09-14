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

import cdj_wav
from cli_error import CliError
from convert.format_policy import classify_source
import convert.plan
from convert import encode
import ffmpeg_tools
from convert.freshness import bind_complete_assignment
from convert.models import Plan, PlannedTrack
from convert.paths import source_key
import converter_manifest
from convert.prepare import prepare
from convert.write import convert_unique
from rekordbox_xml import encode_location
from xml_output import clone_track


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
            info = cdj_wav.parse_wav_info(path)
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
            info = cdj_wav.parse_wav_info(path)
            self.assertEqual(info.format_tag, 0xFFFE)
            self.assertEqual(info.channels, 2)
            self.assertEqual(info.sample_rate, 48000)
            self.assertEqual(info.bits_per_sample, 24)
            self.assertEqual(info.fmt_chunk_size, 40)
            self.assertEqual(info.chunk_ids, ("fmt ", "LIST", "data"))

    def test_trailing_bytes_after_chunks_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trail.wav"
            write_pcm_wav(path)
            path.write_bytes(path.read_bytes() + b"\x00\x01")
            with self.assertRaises(CliError):
                cdj_wav.parse_wav_info(path)
            self.assertFalse(
                cdj_wav.is_cdj_safe_wav(path, bit_depth=16, sample_rate=44100)
            )

    def test_wrong_riff_declared_size_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "badsize.wav"
            write_pcm_wav(path)
            data = bytearray(path.read_bytes())
            # Corrupt RIFF size so declared + 8 != len(data)
            data[4:8] = struct.pack("<I", struct.unpack_from("<I", data, 4)[0] + 10)
            path.write_bytes(data)
            with self.assertRaises(CliError):
                cdj_wav.parse_wav_info(path)
            self.assertFalse(
                cdj_wav.is_cdj_safe_wav(path, bit_depth=16, sample_rate=44100)
            )


class CdjSafeWavTests(unittest.TestCase):
    def test_safe_16bit_stereo_44100_pcm_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "safe.wav"
            write_pcm_wav(path)
            self.assertTrue(cdj_wav.is_cdj_safe_wav(path, bit_depth=16, sample_rate=44100))

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
                self.assertFalse(cdj_wav.is_cdj_safe_wav(path), msg=name)

    def test_missing_file_is_not_safe(self) -> None:
        self.assertFalse(cdj_wav.is_cdj_safe_wav(Path("/no/such/file.wav")))

    def test_parse_wav_info_does_not_read_data_payload(self) -> None:
        """Given a multi-MB CDJ-safe WAV: When parse_wav_info / is_cdj_safe_wav
        run: Then only headers and fmt are read (not the PCM data chunk)."""
        from counting_open import patch_counting_open

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.wav"
            # ~2 MiB PCM payload; streaming parse must not load it all.
            write_pcm_wav(path, frames=512 * 1024)
            self.assertGreater(path.stat().st_size, 1_000_000)

            bytes_read = {"n": 0}
            open_patch, read_bytes_patch = patch_counting_open(path, bytes_read)
            with open_patch, read_bytes_patch:
                info = cdj_wav.parse_wav_info(path)
                self.assertEqual(info.chunk_ids, ("fmt ", "data"))
                self.assertTrue(
                    cdj_wav.is_cdj_safe_wav(path, bit_depth=16, sample_rate=44100)
                )

            # Header (12) + two chunk headers (16) + fmt payload (16) ≈ 44;
            # allow seek/EOF probes but stay far below the PCM size.
            self.assertLess(bytes_read["n"], 256)


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
            info = cdj_wav.parse_wav_info(src)
            self.assertEqual(info.format_tag, 0xFFFE)
            self.assertEqual(info.sample_rate, 48000)
            self.assertFalse(cdj_wav.is_cdj_safe_wav(src))

            convert.plan.run_ffmpeg(src, dest, "pcm_s16le", force=True)

            self.assertTrue(cdj_wav.is_cdj_safe_wav(dest, bit_depth=16, sample_rate=44100))
            out = cdj_wav.parse_wav_info(dest)
            self.assertEqual(out.format_tag, 1)
            self.assertEqual(out.sample_rate, 44100)
            self.assertEqual(out.bits_per_sample, 16)
            self.assertEqual(out.channels, 2)
            self.assertEqual(out.chunk_ids, ("fmt ", "data"))

    def test_ffmpeg_24_48000_wav_is_pcm_not_extensible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.flac"
            dest = root / "out.wav"
            proc = __import__("subprocess").run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=f=440:d=0.2",
                    "-ar",
                    "96000",
                    "-ac",
                    "2",
                    "-sample_fmt",
                    "s32",
                    str(src),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            convert.plan.run_ffmpeg(
                src,
                dest,
                "pcm_s24le",
                force=True,
                sample_rate=48000,
                bit_depth=24,
            )
            self.assertTrue(
                cdj_wav.is_cdj_safe_wav(dest, bit_depth=24, sample_rate=48000)
            )
            out = cdj_wav.parse_wav_info(dest)
            self.assertEqual(out.format_tag, 1)
            self.assertEqual(out.fmt_chunk_size, 16)
            self.assertEqual(out.sample_rate, 48000)
            self.assertEqual(out.bits_per_sample, 24)
            self.assertEqual(out.chunk_ids, ("fmt ", "data"))
            clone = clone_track(
                ET.Element("TRACK", {"Name": "X"}),
                "1",
                dest,
                encode_location(dest),
            )
            self.assertEqual(clone.get("Kind"), "WAV File")
            self.assertEqual(clone.get("SampleRate"), "48000")


class RunFfmpegRewriteGateTests(unittest.TestCase):
    def test_run_ffmpeg_skips_rewrite_when_output_already_cdj_safe(self) -> None:
        """16-bit pcm_s16le from ffmpeg is already fmt+data PCM; do not rewrite."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.wav"
            dest = root / "out.wav"
            write_pcm_wav(src, bits=16, sample_rate=44100)

            class FakeProc:
                def __init__(self, cmd: list[str], *_a: object, **_k: object) -> None:
                    write_pcm_wav(Path(cmd[-1]), bits=16, sample_rate=44100)
                    self.returncode = 0

                def poll(self) -> int:
                    return self.returncode

                def communicate(self) -> tuple[str, str]:
                    return "", ""

                def kill(self) -> None:
                    pass

                def wait(self, timeout: float | None = None) -> int:
                    return self.returncode

            rewrite_calls: list[tuple[Path, Path]] = []
            real_rewrite = encode.rewrite_wav_pcm

            def tracking_rewrite(source: Path, out: Path) -> None:
                rewrite_calls.append((source, out))
                real_rewrite(source, out)

            with mock.patch.object(
                encode.subprocess, "Popen", FakeProc
            ), mock.patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
            ), mock.patch.object(
                ffmpeg_tools, "ffmpeg_supports_soxr", return_value=False
            ), mock.patch.object(
                encode, "rewrite_wav_pcm", side_effect=tracking_rewrite
            ):
                convert.plan.run_ffmpeg(
                    src, dest, "pcm_s16le", force=True, sample_rate=44100, bit_depth=16
                )
            self.assertEqual(rewrite_calls, [])
            self.assertTrue(
                cdj_wav.is_cdj_safe_wav(dest, bit_depth=16, sample_rate=44100)
            )


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

            codec, is_copy, _bits, _rate = classify_source(
                safe, {"codec_name": "pcm_s16le", "sample_fmt": "s16"}
            )
            self.assertTrue(is_copy)
            self.assertEqual(codec, "copy")

            codec, is_copy, _bits, _rate = classify_source(
                unsafe, {"codec_name": "pcm_s16le", "sample_fmt": "s16"}
            )
            self.assertFalse(is_copy)
            self.assertEqual(codec, "pcm_s16le")

            codec, is_copy, bits, rate = classify_source(
                flac,
                {
                    "codec_name": "flac",
                    "sample_fmt": "s32",
                    "bits_per_raw_sample": "24",
                },
            )
            self.assertFalse(is_copy)
            self.assertEqual(codec, "pcm_s24le")
            self.assertEqual((bits, rate), (24, 44100))


class SkipCdjSafeDestTests(unittest.TestCase):
    def test_skips_cdj_safe_dest_reconverts_unsafe_unless_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_safe = root / "a.flac"
            src_unsafe = root / "b.flac"
            src_safe.write_bytes(b"fLaC")
            src_unsafe.write_bytes(b"fLaC")
            media_dir = root / "WAV" / "P"
            media_dir.mkdir(parents=True)
            safe_dest = media_dir / "safe.wav"
            write_pcm_wav(safe_dest)
            unsafe_dest = media_dir / "unsafe.wav"
            write_pcm_wav(unsafe_dest, sample_rate=48000)

            def make_item(src: Path, dest: Path) -> PlannedTrack:
                el = ET.Element(
                    "TRACK", {"TrackID": "1", "Location": encode_location(src)}
                )
                return PlannedTrack(
                    source_el=el,
                    source_path=src,
                    dest_path=dest,
                    dest_location=encode_location(dest),
                    dest_name=dest.name,
                    codec="pcm_s16le",
                    passthrough=False,
                    noop=False,
                    bit_depth=16,
                    sample_rate=44100,
                )

            safe_item = make_item(src_safe, safe_dest)
            unsafe_item = make_item(src_unsafe, unsafe_dest)
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root / "WAV",
                media_dir=media_dir,
                output=root / "o.xml",
                tracks=[safe_item, unsafe_item],
                unique=[safe_item, unsafe_item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
            )
            plan.manifest = converter_manifest.empty_manifest()
            bind_complete_assignment(plan.manifest, safe_item, "WAV/P/safe.wav")
            converted: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                converted.append(dest)
                write_pcm_wav(dest)

            with mock.patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = convert_unique(plan, force=False)
            self.assertEqual(stats.skipped, 1)
            self.assertEqual(stats.converted, 1)
            self.assertEqual(converted, [unsafe_dest])

            converted.clear()
            with mock.patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = convert_unique(plan, force=True)
            self.assertEqual(stats.converted, 2)
            self.assertCountEqual(converted, [safe_dest, unsafe_dest])

    def test_sixteen_44100_dest_does_not_skip_when_effective_is_24_48(self) -> None:
        """Raising the ceiling must rebuild a previously reduced dest."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            media_dir = root / "WAV" / "P"
            media_dir.mkdir(parents=True)
            dest = media_dir / "a.wav"
            write_pcm_wav(dest)  # existing 16/44100
            el = ET.Element(
                "TRACK", {"TrackID": "1", "Location": encode_location(src)}
            )
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s24le",
                passthrough=False,
                noop=False,
                bit_depth=24,
                sample_rate=48000,
            )
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root / "WAV",
                media_dir=media_dir,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
                max_bit_depth=24,
                max_sample_rate=48000,
            )
            converted: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                converted.append(dest_path)
                write_pcm_wav(dest_path, bits=24, sample_rate=48000)

            with mock.patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = convert_unique(plan, force=False)
            self.assertEqual(stats.skipped, 0)
            self.assertEqual(stats.converted, 1)
            self.assertEqual(converted, [dest])

    def test_failed_force_rebuild_preserves_previously_valid_dest(self) -> None:
        """Given a valid dest: When force convert fails mid-way: Then dest
        content is unchanged (temp/sidecar cleaned; final dest not unlinked)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            media_dir = root / "WAV" / "P"
            media_dir.mkdir(parents=True)
            dest = media_dir / "a.wav"
            write_pcm_wav(dest, bits=16, sample_rate=44100)
            prior = dest.read_bytes()
            el = ET.Element(
                "TRACK", {"TrackID": "1", "Location": encode_location(src)}
            )
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
            )
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root / "WAV",
                media_dir=media_dir,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
            )

            def boom(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                raise CliError(f"encode failed for {source}")

            with mock.patch.object(convert.plan, "run_ffmpeg", side_effect=boom):
                stats = convert_unique(plan, force=True)
            self.assertEqual(len(stats.errors), 1)
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), prior)

    def test_classify_preserves_16_44100_under_24_48_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flac = root / "track.flac"
            flac.write_bytes(b"fLaC")
            codec, is_copy, bits, rate = classify_source(
                flac,
                {
                    "codec_name": "flac",
                    "sample_fmt": "s16",
                    "bits_per_raw_sample": "16",
                    "sample_rate": "44100",
                    "channels": "2",
                },
                max_bit_depth=24,
                max_sample_rate=48000,
            )
            self.assertFalse(is_copy)
            self.assertEqual(codec, "pcm_s16le")
            self.assertEqual((bits, rate), (16, 44100))


class NoopUnsafeInPlaceTests(unittest.TestCase):
    def test_prepare_errors_when_unsafe_wav_source_equals_dest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav_dir = root / "out"
            playlist = "Set"
            # Dest is WAV/Artist - Name.wav — put out-of-profile source there.
            src = wav_dir / "WAV" / "Unknown Artist - t.wav"
            src.parent.mkdir(parents=True)
            write_pcm_wav(src, sample_rate=96000)
            xml_path = root / "c.xml"
            xml_path.write_text(
                f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="t" Location="{encode_location(src)}" Kind="WAV File"/>
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
            with mock.patch.object(ffmpeg_tools, "require_tools", return_value=[]):
                _, errors = prepare(
                    xml_path, playlist, wav_dir, root / "import.xml"
                )
            self.assertTrue(
                any("refusing to convert in place" in e for e in errors),
                errors,
            )


class RewriteContainerTests(unittest.TestCase):
    def test_rewrite_container_keeps_wav_pcm_without_ffmpeg(self) -> None:
        """Given a complete WAV dest whose recipe revision differs: When convert:
        Then dest PCM is unchanged, extra chunks are dropped, and ffmpeg is not
        called."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            pcm = b"\x11\x22" * 16
            write_pcm_wav(
                dest,
                extra_chunks=[(b"LIST", b"INFO" + b"\x00" * 12)],
                frames=8,
            )
            # Overwrite the data payload with a recognizable pattern.
            raw = bytearray(dest.read_bytes())
            data_at = raw.find(b"data")
            size = struct.unpack_from("<I", raw, data_at + 4)[0]
            raw[data_at + 8 : data_at + 8 + size] = (pcm * 8)[:size]
            dest.write_bytes(raw)
            before_pcm = (pcm * 8)[:size]
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
                manifest=converter_manifest.empty_manifest(),
            )
            bind_complete_assignment(plan.manifest, item, "WAV/A.wav")
            plan.manifest.tracks[source_key(src)]["wav"]["recipe"]["revision"] = 2
            encoded: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                encoded.append(dest_path)
                dest_path.write_bytes(b"OVERWRITTEN")

            with mock.patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = convert_unique(plan, force=False)
            self.assertEqual(encoded, [])
            self.assertEqual(stats.errors, [])
            info = cdj_wav.parse_wav_info(dest)
            self.assertEqual(info.chunk_ids, ("fmt ", "data"))
            out_raw = dest.read_bytes()
            out_at = out_raw.find(b"data")
            out_size = struct.unpack_from("<I", out_raw, out_at + 4)[0]
            self.assertEqual(out_raw[out_at + 8 : out_at + 8 + out_size], before_pcm)

    def test_unsafe_dest_rewrite_falls_back_to_transcode(self) -> None:
        """Given rewrite_container but dest is not a valid WAV: When convert:
        Then ffmpeg transcodes from the source and dest is not left as garbage."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            dest.write_bytes(b"not a wav")
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
                manifest=converter_manifest.empty_manifest(),
            )
            bind_complete_assignment(plan.manifest, item, "WAV/A.wav")
            plan.manifest.tracks[source_key(src)]["wav"]["recipe"]["revision"] = 2
            encoded: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                encoded.append(dest_path)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(b"RIFF-FROM-SOURCE")

            with mock.patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = convert_unique(plan, force=False)
            self.assertEqual(encoded, [dest])
            self.assertEqual(stats.errors, [])
            self.assertEqual(stats.converted, 1)
            self.assertEqual(dest.read_bytes(), b"RIFF-FROM-SOURCE")


if __name__ == "__main__":
    unittest.main()
