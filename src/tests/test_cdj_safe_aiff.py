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

# IEEE 80-bit extended floats for common rates (big-endian).
RATE_44100 = bytes.fromhex("400eac44000000000000")
RATE_48000 = bytes.fromhex("400ebb80000000000000")
RATE_22050 = bytes.fromhex("400dac44000000000000")
RATE_96000 = bytes.fromhex("400fbb80000000000000")


def _pack_extended_rate(rate_bytes: bytes) -> bytes:
    assert len(rate_bytes) == 10
    return rate_bytes


def write_pcm_aiff(
    path: Path,
    *,
    sample_rate_bytes: bytes = RATE_44100,
    channels: int = 2,
    bits: int = 16,
    frames: int = 8,
    form_type: bytes = b"AIFF",
    extra_chunks: list[tuple[bytes, bytes]] | None = None,
    include_id3: bytes | None = None,
    ssnd_offset: int = 0,
    ssnd_block_size: int = 0,
    comm_size_override: int | None = None,
    num_sample_frames: int | None = None,
) -> None:
    """Write a minimal uncompressed AIFF (or AIFC) for header tests."""
    sample_frames = frames if num_sample_frames is None else num_sample_frames
    pcm = b"\x00" * (frames * channels * (bits // 8))
    comm_payload = struct.pack(">hIh", channels, sample_frames, bits) + _pack_extended_rate(
        sample_rate_bytes
    )
    if form_type == b"AIFC":
        # Compression type + name (empty pstring)
        comm_payload += b"NONE" + b"\x00"
    chunks: list[tuple[bytes, bytes]] = []
    if form_type == b"AIFC":
        chunks.append((b"FVER", struct.pack(">I", 0xA2805140)))
    if extra_chunks:
        for cid, payload in extra_chunks:
            if cid == b"COMM":
                continue
            chunks.append((cid, payload))
    comm_size = len(comm_payload) if comm_size_override is None else comm_size_override
    chunks.append((b"COMM", comm_payload if comm_size_override is None else comm_payload[:comm_size].ljust(comm_size, b"\x00")))
    ssnd_payload = struct.pack(">II", ssnd_offset, ssnd_block_size) + pcm
    chunks.append((b"SSND", ssnd_payload))
    if include_id3 is not None:
        chunks.append((b"ID3 ", include_id3))
    body = b""
    for cid, payload in chunks:
        body += cid + struct.pack(">I", len(payload)) + payload
        if len(payload) % 2:
            body += b"\x00"
    form = form_type + body
    path.write_bytes(b"FORM" + struct.pack(">I", len(form)) + form)


class CdjSafeAiffTests(unittest.TestCase):
    def test_safe_16_44100_and_24_48000_stereo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a16 = root / "a16.aiff"
            write_pcm_aiff(a16, sample_rate_bytes=RATE_44100, bits=16)
            self.assertTrue(rb.is_cdj_safe_aiff(a16, bit_depth=16, sample_rate=44100))
            a24 = root / "a24.aiff"
            write_pcm_aiff(a24, sample_rate_bytes=RATE_48000, bits=24)
            self.assertTrue(
                rb.is_cdj_safe_aiff(a24, bit_depth=24, sample_rate=48000)
            )
            a16_48 = root / "a16_48.aiff"
            write_pcm_aiff(a16_48, sample_rate_bytes=RATE_48000, bits=16)
            self.assertTrue(
                rb.is_cdj_safe_aiff(a16_48, bit_depth=16, sample_rate=48000)
            )
            a24_44 = root / "a24_44.aiff"
            write_pcm_aiff(a24_44, sample_rate_bytes=RATE_44100, bits=24)
            self.assertTrue(
                rb.is_cdj_safe_aiff(a24_44, bit_depth=24, sample_rate=44100)
            )

    def test_name_chunk_still_audio_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "named.aiff"
            write_pcm_aiff(path, extra_chunks=[(b"NAME", b"t\x00")])
            self.assertTrue(rb.is_cdj_safe_aiff(path, bit_depth=16, sample_rate=44100))

    def test_rejects_aifc_wrong_rate_depth_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases: list[tuple[str, dict]] = [
                ("aifc.aiff", dict(form_type=b"AIFC")),
                ("rate22.aiff", dict(sample_rate_bytes=RATE_22050)),
                ("rate96.aiff", dict(sample_rate_bytes=RATE_96000)),
                ("bits32.aiff", dict(bits=32)),
                ("mono.aiff", dict(channels=1)),
            ]
            for name, kwargs in cases:
                path = root / name
                write_pcm_aiff(path, **kwargs)
                self.assertFalse(rb.is_cdj_safe_aiff(path), msg=name)
            self.assertFalse(rb.is_cdj_safe_aiff(Path("/no/such/file.aiff")))


class DestNameAndClassifyAiffTests(unittest.TestCase):
    def test_dest_name_for_aiff_and_default_wav(self) -> None:
        src = Path("/music/Track One.flac")
        self.assertEqual(rb.dest_name_for(src), "Track One.wav")
        self.assertEqual(rb.dest_name_for(src, output_format="wav"), "Track One.wav")
        self.assertEqual(rb.dest_name_for(src, output_format="aiff"), "Track One.aiff")

    def test_classify_aiff_passthrough_and_transcode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            safe = root / "safe.aiff"
            write_pcm_aiff(safe, extra_chunks=[(b"NAME", b"x\x00")])
            flac = root / "t.flac"
            flac.write_bytes(b"fLaC")
            wav = root / "safe.wav"
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from test_cdj_safe_wav import write_pcm_wav

            write_pcm_wav(wav)

            codec, is_copy, bits, rate = rb.classify_source(
                safe,
                {"codec_name": "pcm_s16be", "sample_fmt": "s16"},
                output_format="aiff",
            )
            self.assertTrue(is_copy)
            self.assertEqual(codec, "copy")
            self.assertEqual((bits, rate), (16, 44100))

            codec, is_copy, bits, rate = rb.classify_source(
                flac,
                {
                    "codec_name": "flac",
                    "sample_fmt": "s16",
                    "bits_per_raw_sample": "16",
                    "sample_rate": "44100",
                    "channels": "2",
                },
                output_format="aiff",
            )
            self.assertFalse(is_copy)
            self.assertEqual(codec, "pcm_s16be")

            # CDJ-safe WAV must not be copied when outputting AIFF.
            codec, is_copy, bits, rate = rb.classify_source(
                wav,
                {"codec_name": "pcm_s16le", "sample_fmt": "s16"},
                output_format="aiff",
            )
            self.assertFalse(is_copy)
            self.assertEqual(codec, "pcm_s16be")

            # WAV output still copies only CDJ-safe WAV.
            codec, is_copy, bits, rate = rb.classify_source(
                wav, {"codec_name": "pcm_s16le", "sample_fmt": "s16"}
            )
            self.assertTrue(is_copy)


class InPlaceAiffTests(unittest.TestCase):
    def test_prepare_refuses_in_place_safe_aiff_with_mismatched_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav_dir = root / "out"
            playlist = "Set"
            playlist_dir = wav_dir / playlist
            playlist_dir.mkdir(parents=True)
            src = playlist_dir / "track.aiff"
            write_pcm_aiff(src)
            xml_path = root / "c.xml"
            xml_path.write_text(
                f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Expected Title" Artist="A"
           Location="{rb.encode_location(src)}" Kind="AIFF File"/>
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
                    xml_path,
                    playlist,
                    wav_dir,
                    root / "import.xml",
                    output_format="aiff",
                )
            self.assertTrue(
                any("refusing to convert in place" in e for e in errors),
                errors,
            )


class Id3AndConvertAiffTests(unittest.TestCase):
    def test_id3_utf16_ukrainian_title_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.aiff"
            write_pcm_aiff(src)
            dest = root / "out.aiff"
            el = ET.Element(
                "TRACK",
                {
                    "Name": "Пісня",
                    "Artist": "Артист",
                    "Album": "Альбом",
                },
            )
            shutil_copy = __import__("shutil").copy2
            shutil_copy(src, dest)
            rb.write_aiff_id3(dest, el, None)
            self.assertTrue(
                rb.is_cdj_safe_aiff(dest, bit_depth=16, sample_rate=44100)
            )
            self.assertTrue(
                rb._is_canonical_aiff_output(
                    dest, el, None, bit_depth=16, sample_rate=44100
                )
            )
            tag = rb._extract_id3_chunk(dest)
            assert tag is not None
            text, cover = rb._read_id3_frames(tag)
            self.assertEqual(text.get("TIT2"), "Пісня")
            self.assertEqual(text.get("TPE1"), "Артист")
            self.assertIsNone(cover)
            # version byte 3
            self.assertEqual(tag[3], 3)

    def test_passthrough_preserves_ssnd_and_skips_when_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "safe.aiff"
            write_pcm_aiff(src, extra_chunks=[(b"NAME", b"x\x00")])
            before = rb._ssnd_pcm_bytes(src)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            dest = root / "out.aiff"
            rb.write_aiff_output(
                src, dest, el, passthrough=True, codec=None,
                bit_depth=16, sample_rate=44100,
            )
            self.assertEqual(rb._ssnd_pcm_bytes(dest), before)
            self.assertTrue(
                rb._is_canonical_aiff_output(
                    dest, el, None, bit_depth=16, sample_rate=44100
                )
            )
            plan_item = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=rb.encode_location(dest),
                dest_name=dest.name,
                codec=None,
                copy_wav=True,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
            )
            plan = rb.Plan(
                playlist_name="P",
                wav_playlist_name="P [AIFF]",
                wav_dir=root,
                playlist_dir=root,
                output=root / "o.xml",
                tracks=[plan_item],
                unique=[plan_item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
            )
            stats = rb.convert_unique(plan, force=False)
            self.assertEqual(stats.skipped, 1)

    def test_aiff_24_48_dest_does_not_skip_when_effective_is_16_44100(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.aiff"
            write_pcm_aiff(src, sample_rate_bytes=RATE_44100, bits=16)
            dest = root / "out.aiff"
            write_pcm_aiff(dest, sample_rate_bytes=RATE_48000, bits=24)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            rb.write_aiff_id3(dest, el, None)
            self.assertTrue(
                rb._is_canonical_aiff_output(
                    dest, el, None, bit_depth=24, sample_rate=48000
                )
            )
            self.assertFalse(
                rb._is_canonical_aiff_output(
                    dest, el, None, bit_depth=16, sample_rate=44100
                )
            )
            plan_item = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=rb.encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s16be",
                copy_wav=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
            )
            plan = rb.Plan(
                playlist_name="P",
                wav_playlist_name="P [AIFF]",
                wav_dir=root,
                playlist_dir=root,
                output=root / "o.xml",
                tracks=[plan_item],
                unique=[plan_item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
                output_format="aiff",
            )
            wrote: list[Path] = []

            def fake_write(*_a, **_k):
                wrote.append(dest)

            with mock.patch.object(rb, "write_aiff_output", side_effect=fake_write):
                stats = rb.convert_unique(plan, force=False)
            self.assertEqual(stats.skipped, 0)
            self.assertEqual(stats.converted, 1)
            self.assertEqual(wrote, [dest])

    def test_classify_aiff_default_ceiling_is_24_48000(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "hi.aiff"
            write_pcm_aiff(src, sample_rate_bytes=RATE_48000, bits=24)
            codec, is_copy, bits, rate = rb.classify_source(
                src,
                {
                    "codec_name": "pcm_s24be",
                    "sample_fmt": "s24",
                    "bits_per_raw_sample": "24",
                    "sample_rate": "48000",
                    "channels": "2",
                },
                output_format="aiff",
            )
            self.assertTrue(is_copy)
            self.assertEqual(codec, "copy")
            self.assertEqual((bits, rate), (24, 48000))

            low = root / "lo.aiff"
            write_pcm_aiff(low, sample_rate_bytes=RATE_44100, bits=16)
            codec, is_copy, bits, rate = rb.classify_source(
                low,
                {
                    "codec_name": "pcm_s16be",
                    "sample_fmt": "s16",
                    "bits_per_raw_sample": "16",
                    "sample_rate": "44100",
                    "channels": "2",
                },
                output_format="aiff",
            )
            self.assertTrue(is_copy)
            self.assertEqual((bits, rate), (16, 44100))

            codec, is_copy, bits, rate = rb.classify_source(
                src,
                {
                    "codec_name": "pcm_s24be",
                    "sample_fmt": "s24",
                    "bits_per_raw_sample": "24",
                    "sample_rate": "48000",
                    "channels": "2",
                },
                output_format="aiff",
                max_bit_depth=16,
                max_sample_rate=44100,
            )
            self.assertFalse(is_copy)
            self.assertEqual(codec, "pcm_s16be")
            self.assertEqual((bits, rate), (16, 44100))

    def test_ffmpeg_16_44100_flac_stays_16_44100_aiff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.flac"
            dest = root / "out.aiff"
            proc = __import__("subprocess").run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=f=440:d=0.2",
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    "-sample_fmt",
                    "s16",
                    str(src),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            el = ET.Element("TRACK", {"Name": "Tone", "Artist": "Test"})
            rb.write_aiff_output(
                src, dest, el, passthrough=False, codec="pcm_s16be",
                bit_depth=16, sample_rate=44100,
            )
            info = rb._parse_aiff_audio(dest)
            self.assertEqual(info.bits_per_sample, 16)
            self.assertEqual(info.sample_rate_bytes, RATE_44100)
            self.assertTrue(
                rb._is_canonical_aiff_output(
                    dest, el, None, bit_depth=16, sample_rate=44100
                )
            )

    def test_ffmpeg_96k_24bit_flac_to_aiff_24_48000(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.flac"
            dest = root / "out.aiff"
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
            el = ET.Element("TRACK", {"Name": "HiRes", "Artist": "Test"})
            rb.write_aiff_output(
                src,
                dest,
                el,
                passthrough=False,
                codec="pcm_s24be",
                bit_depth=24,
                sample_rate=48000,
            )
            info = rb._parse_aiff_audio(dest)
            self.assertEqual(info.bits_per_sample, 24)
            self.assertEqual(info.sample_rate_bytes, RATE_48000)
            self.assertTrue(
                rb._is_canonical_aiff_output(
                    dest, el, None, bit_depth=24, sample_rate=48000
                )
            )
            size, bitrate, sample_rate = rb.probe_dest_tech(dest)
            self.assertEqual(sample_rate, "48000")
            clone = rb.clone_track(el, "1", dest, rb.encode_location(dest))
            self.assertEqual(clone.get("Kind"), "AIFF File")
            self.assertEqual(clone.get("SampleRate"), "48000")


class ApplyXmlRefreshTests(unittest.TestCase):
    def test_apply_xml_refreshes_name_keeps_track_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "t.aiff"
            write_pcm_aiff(dest)
            rb.write_aiff_id3(
                dest, ET.Element("TRACK", {"Name": "Old"}), None
            )
            source_old = ET.Element(
                "TRACK",
                {
                    "TrackID": "9",
                    "Name": "Old",
                    "Location": rb.encode_location(dest),
                },
            )
            source_new = ET.Element(
                "TRACK",
                {
                    "TrackID": "9",
                    "Name": "New Title",
                    "Artist": "X",
                    "Location": rb.encode_location(Path("/orig/t.flac")),
                },
            )
            output_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            collection = ET.SubElement(output_root, "COLLECTION", {"Entries": "1"})
            existing = rb.clone_track(
                source_old, "42", dest, rb.encode_location(dest)
            )
            collection.append(existing)
            item = rb.PlannedTrack(
                source_el=source_new,
                source_path=Path("/orig/t.flac"),
                dest_path=dest,
                dest_location=rb.encode_location(dest),
                dest_name=dest.name,
                codec=None,
                copy_wav=True,
                noop=False,
            )
            plan = rb.Plan(
                playlist_name="P",
                wav_playlist_name="P [AIFF]",
                wav_dir=root,
                playlist_dir=root,
                output=root / "import.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=output_root,
                output_existed=True,
            )
            with mock.patch.object(rb, "probe_dest_tech", return_value=("1", "1411", "44100")):
                rb.apply_xml(plan)
            tracks = output_root.findall("COLLECTION/TRACK")
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].get("TrackID"), "42")
            self.assertEqual(tracks[0].get("Name"), "New Title")
            self.assertEqual(tracks[0].get("Artist"), "X")
            self.assertEqual(tracks[0].get("Kind"), "AIFF File")


if __name__ == "__main__":
    unittest.main()
