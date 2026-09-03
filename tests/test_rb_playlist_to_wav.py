#!/usr/bin/env python3
from __future__ import annotations

import io
import os
import tempfile
import unicodedata
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import rb_playlist_to_wav as rb

FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="219211420" Name="Bestial" Artist="ABSL" Composer=""
           Album="It's just a bad dream" Grouping="" Genre="Electronic"
           Kind="FLAC File" Size="40888414" TotalTime="328" DiscNumber="0"
           TrackNumber="7" Year="2023" AverageBpm="148.00" DateAdded="2024-06-14"
           BitRate="0" SampleRate="44100" Comments="exystence.net" PlayCount="19"
           Rating="51" Location="{loc_a}" Remixer="" Tonality="Bbm"
           Label="Mama told ya" Mix="" Colour="0xFF0000">
      <TEMPO Inizio="0.027" Bpm="148.00" Metro="4/4" Battito="1"/>
      <POSITION_MARK Name="cue" Type="0" Start="0.027" Num="-1" Red="255" Green="0" Blue="0"/>
      <EXTRA Foo="bar"/>
    </TRACK>
    <TRACK TrackID="58834508" Name="Revelation" Artist="Shogan" Composer=""
           Album="Hits" Grouping="" Genre="Trance" Kind="FLAC File" Size="1"
           TotalTime="100" DiscNumber="0" TrackNumber="1" Year="2020"
           AverageBpm="140.00" DateAdded="2024-12-31" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="0"
           Location="{loc_b}" Remixer="" Tonality="Bm" Label="" Mix="">
      <TEMPO Inizio="0.000" Bpm="140.00" Metro="4/4" Battito="1"/>
    </TRACK>
    <TRACK TrackID="115068759" Name="Movement" Artist="Quantum" Composer=""
           Album="Hits" Grouping="" Genre="Trance" Kind="FLAC File" Size="1"
           TotalTime="100" DiscNumber="0" TrackNumber="18" Year="2020"
           AverageBpm="145.00" DateAdded="2024-12-31" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="0"
           Location="{loc_c}" Remixer="" Tonality="G" Label="" Mix="">
      <TEMPO Inizio="0.413" Bpm="145.00" Metro="4/4" Battito="2"/>
    </TRACK>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Intelligent playlists" Type="0" Count="1">
        <NODE Name="Untitled Intelligent List" Type="1" KeyType="0" Entries="3">
          <TRACK Key="219211420"/>
          <TRACK Key="58834508"/>
          <TRACK Key="115068759"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


def flac_probe(bits: int = 24) -> dict:
    fmt = "s32" if bits == 24 else "s16"
    return {
        "format": {"format_name": "flac"},
        "streams": [
            {
                "codec_name": "flac",
                "sample_fmt": fmt,
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": str(bits),
            }
        ],
    }


def wav_probe(bits: int = 24) -> dict:
    codec = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}[bits]
    fmt = "s16" if bits == 16 else "s32"
    return {
        "format": {"format_name": "wav"},
        "streams": [
            {
                "codec_name": codec,
                "sample_fmt": fmt,
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": str(bits),
            }
        ],
    }


def write_flac(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC")


class CodecMapTests(unittest.TestCase):
    def test_16_24_32_float(self) -> None:
        self.assertEqual(
            rb.pcm_codec_for_stream({"sample_fmt": "s16", "bits_per_raw_sample": "16"}),
            "pcm_s16le",
        )
        self.assertEqual(
            rb.pcm_codec_for_stream({"sample_fmt": "s32", "bits_per_raw_sample": "24"}),
            "pcm_s24le",
        )
        self.assertEqual(
            rb.pcm_codec_for_stream({"sample_fmt": "s32", "bits_per_raw_sample": "32"}),
            "pcm_s32le",
        )
        self.assertEqual(
            rb.pcm_codec_for_stream({"sample_fmt": "fltp"}),
            "pcm_f32le",
        )

    def test_unknown_depth_fails(self) -> None:
        with self.assertRaises(rb.CliError):
            rb.pcm_codec_for_stream({"sample_fmt": "s32"})
        with self.assertRaises(rb.CliError):
            rb.pcm_codec_for_stream({"sample_fmt": "u8", "bits_per_raw_sample": "8"})


class LocationTests(unittest.TestCase):
    def test_roundtrip_percent_encoding(self) -> None:
        path = Path("/Users/me/It's just a bad dream/07 - Bestial.flac")
        url = rb.encode_location(path)
        self.assertTrue(url.startswith("file://localhost/"))
        self.assertIn("It%27s", url)
        self.assertIn("%20", url)
        self.assertEqual(rb.decode_location(url), path)

    def test_decode_file_triple_slash(self) -> None:
        self.assertEqual(
            rb.decode_location("file:///Users/me/track.flac"),
            Path("/Users/me/track.flac"),
        )

    def test_invalid_url(self) -> None:
        self.assertIsNone(rb.decode_location("http://example.com/a.flac"))
        self.assertIsNone(rb.decode_location(""))
        self.assertIsNone(rb.decode_location("C:/not/a/url.flac"))


class CollisionKeyTests(unittest.TestCase):
    def test_case_and_nfd(self) -> None:
        self.assertEqual(rb.collision_key("Intro.wav"), rb.collision_key("intro.wav"))
        nfc = unicodedata.normalize("NFC", "café.wav")
        nfd = unicodedata.normalize("NFD", "café.wav")
        self.assertNotEqual(nfc, nfd)
        self.assertEqual(rb.collision_key(nfc), rb.collision_key(nfd))


class XmlFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.music = self.root / "music"
        self.a = self.music / "It's just a bad dream" / "07 - Bestial.flac"
        self.b = self.music / "Shogan" / "Revelation.flac"
        self.c = self.music / "Quantum" / "Movement.flac"
        for p in (self.a, self.b, self.c):
            write_flac(p)
        self.xml_path = self.root / "collection.xml"
        xml = FIXTURE.format(
            loc_a=rb.encode_location(self.a),
            loc_b=rb.encode_location(self.b),
            loc_c=rb.encode_location(self.c),
        )
        self.xml_path.write_text(xml, encoding="utf-8")
        self.wav_dir = self.root / "WAV"
        self.output = self.root / "out.xml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _probe(self, path: Path) -> dict:
        if path.suffix.lower() == ".wav":
            return wav_probe()
        return flac_probe()

    def test_recursive_playlist_lookup(self) -> None:
        root = rb.load_dj_playlists(self.xml_path)
        found = rb.find_playlists_by_name(root, "Untitled Intelligent List")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].get("Entries"), "3")
        self.assertEqual(rb.find_playlists_by_name(root, "missing"), [])

    def test_duplicate_playlist_name(self) -> None:
        root = rb.load_dj_playlists(self.xml_path)
        playlists = root.find("PLAYLISTS/NODE")
        assert playlists is not None
        ET.SubElement(playlists, "NODE", {"Name": "Untitled Intelligent List", "Type": "1", "KeyType": "0", "Entries": "0"})
        dup = self.root / "dup.xml"
        ET.ElementTree(root).write(dup, encoding="UTF-8", xml_declaration=True)
        with patch.object(rb, "require_tools", return_value=[]):
            _, errors = rb.prepare(dup, "Untitled Intelligent List", self.wav_dir, self.output)
        self.assertTrue(any("duplicate playlist name" in e for e in errors))

    def test_missing_collection_key(self) -> None:
        root = rb.load_dj_playlists(self.xml_path)
        node = rb.find_playlists_by_name(root, "Untitled Intelligent List")[0]
        ET.SubElement(node, "TRACK", {"Key": "999"})
        bad = self.root / "missing-key.xml"
        ET.ElementTree(root).write(bad, encoding="UTF-8", xml_declaration=True)
        with patch.object(rb, "require_tools", return_value=[]), patch.object(
            rb, "run_ffprobe", side_effect=self._probe
        ):
            _, errors = rb.prepare(bad, "Untitled Intelligent List", self.wav_dir, self.output)
        self.assertTrue(any("missing collection track" in e for e in errors))

    def test_invalid_url(self) -> None:
        root = rb.load_dj_playlists(self.xml_path)
        track = root.find("COLLECTION/TRACK")
        assert track is not None
        track.set("Location", "http://example.com/x.flac")
        bad = self.root / "bad-url.xml"
        ET.ElementTree(root).write(bad, encoding="UTF-8", xml_declaration=True)
        with patch.object(rb, "require_tools", return_value=[]), patch.object(
            rb, "run_ffprobe", side_effect=self._probe
        ):
            _, errors = rb.prepare(bad, "Untitled Intelligent List", self.wav_dir, self.output)
        self.assertTrue(any("invalid Rekordbox file URL" in e for e in errors))

    def test_collisions_case_and_nfd(self) -> None:
        intro = self.music / "one" / "Intro.flac"
        intro2 = self.music / "two" / "intro.flac"
        cafe_nfc = self.music / "n1" / (unicodedata.normalize("NFC", "café") + ".flac")
        cafe_nfd = self.music / "n2" / (unicodedata.normalize("NFD", "café") + ".flac")
        for p in (intro, intro2, cafe_nfc, cafe_nfd):
            write_flac(p)
        extra = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="4">
    <TRACK TrackID="1" Name="a" Location="{rb.encode_location(intro)}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="b" Location="{rb.encode_location(intro2)}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="c" Location="{rb.encode_location(cafe_nfc)}" Kind="FLAC File"/>
    <TRACK TrackID="4" Name="d" Location="{rb.encode_location(cafe_nfd)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Clash" Type="1" KeyType="0" Entries="4">
        <TRACK Key="1"/><TRACK Key="2"/><TRACK Key="3"/><TRACK Key="4"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "clash.xml"
        path.write_text(extra, encoding="utf-8")
        with patch.object(rb, "require_tools", return_value=[]), patch.object(
            rb, "run_ffprobe", side_effect=self._probe
        ):
            _, errors = rb.prepare(path, "Clash", self.wav_dir, self.output)
        joined = "\n".join(errors)
        self.assertIn("Filename collision", joined)
        self.assertIn("Intro.wav", joined)
        self.assertTrue("café.wav" in joined or "cafe" in joined.casefold())

    def test_unknown_fields_preserved_and_ids_start_at_1(self) -> None:
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(rb, "require_tools", return_value=[]), patch.object(
            rb, "run_ffprobe", side_effect=self._probe
        ), patch.object(rb, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_valid_pcm_wav", return_value=False
        ):
            rc = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(rc, 0)
        out = ET.parse(self.output).getroot()
        tracks = out.findall("COLLECTION/TRACK")
        self.assertEqual(len(tracks), 3)
        self.assertEqual([t.get("TrackID") for t in tracks], ["1", "2", "3"])
        first = tracks[0]
        self.assertEqual(first.get("Colour"), "0xFF0000")
        self.assertEqual(first.get("Rating"), "51")
        self.assertEqual(first.get("Kind"), "WAV File")
        loc = first.get("Location") or ""
        self.assertIn("/WAV/Untitled%20Intelligent%20List/", loc)
        self.assertTrue((self.wav_dir / "Untitled Intelligent List" / "07 - Bestial.wav").is_file())
        extra = first.find("EXTRA")
        self.assertIsNotNone(extra)
        assert extra is not None
        self.assertEqual(extra.get("Foo"), "bar")
        self.assertIsNotNone(first.find("TEMPO"))
        self.assertIsNotNone(first.find("POSITION_MARK"))
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")
        self.assertEqual(len(pl), 1)
        self.assertEqual([t.get("Key") for t in pl[0].findall("TRACK")], ["1", "2", "3"])

    def test_location_reuse_and_rerun_extends_playlist(self) -> None:
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        patches = (
            patch.object(rb, "require_tools", return_value=[]),
            patch.object(rb, "run_ffprobe", side_effect=self._probe),
            patch.object(rb, "run_ffmpeg", side_effect=fake_ffmpeg),
            patch.object(rb, "is_valid_pcm_wav", return_value=False),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            self.assertEqual(
                rb.main(
                    [
                        "--xml",
                        str(self.xml_path),
                        "--playlist",
                        "Untitled Intelligent List",
                        "--wav-dir",
                        str(self.wav_dir),
                        "--output",
                        str(self.output),
                    ]
                ),
                0,
            )
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)

        # Drop last track from source playlist; re-run must keep existing WAV playlist entries.
        src = ET.parse(self.xml_path).getroot()
        node = rb.find_playlists_by_name(src, "Untitled Intelligent List")[0]
        for child in list(node):
            node.remove(child)
        ET.SubElement(node, "TRACK", {"Key": "219211420"})
        ET.SubElement(node, "TRACK", {"Key": "58834508"})
        node.set("Entries", "2")
        slim = self.root / "slim.xml"
        ET.ElementTree(src).write(slim, encoding="UTF-8", xml_declaration=True)

        with patches[0], patches[1], patches[2], patches[3]:
            self.assertEqual(
                rb.main(
                    [
                        "--xml",
                        str(slim),
                        "--playlist",
                        "Untitled Intelligent List",
                        "--wav-dir",
                        str(self.wav_dir),
                        "--output",
                        str(self.output),
                    ]
                ),
                0,
            )
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")[0]
        self.assertEqual([t.get("Key") for t in pl.findall("TRACK")], ["1", "2", "3"])

        # Add a fourth source track; re-run appends one collection TRACK and one playlist entry.
        d = self.music / "New" / "Added.flac"
        write_flac(d)
        src = ET.parse(self.xml_path).getroot()
        collection = src.find("COLLECTION")
        assert collection is not None
        ET.SubElement(
            collection,
            "TRACK",
            {
                "TrackID": "42",
                "Name": "Added",
                "Kind": "FLAC File",
                "Location": rb.encode_location(d),
                "BitRate": "0",
                "SampleRate": "44100",
            },
        )
        node = rb.find_playlists_by_name(src, "Untitled Intelligent List")[0]
        ET.SubElement(node, "TRACK", {"Key": "42"})
        grown = self.root / "grown.xml"
        ET.ElementTree(src).write(grown, encoding="UTF-8", xml_declaration=True)
        with patches[0], patches[1], patches[2], patches[3]:
            self.assertEqual(
                rb.main(
                    [
                        "--xml",
                        str(grown),
                        "--playlist",
                        "Untitled Intelligent List",
                        "--wav-dir",
                        str(self.wav_dir),
                        "--output",
                        str(self.output),
                    ]
                ),
                0,
            )
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 4)
        ids = [t.get("TrackID") for t in out.findall("COLLECTION/TRACK")]
        self.assertEqual(ids[:3], ["1", "2", "3"])
        self.assertEqual(ids[3], "4")
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")[0]
        self.assertEqual([t.get("Key") for t in pl.findall("TRACK")], ["1", "2", "3", "4"])

    def test_invalid_existing_output_not_clobbered(self) -> None:
        self.output.write_text("not a rekordbox collection", encoding="utf-8")
        with patch.object(rb, "require_tools", return_value=[]), patch.object(
            rb, "run_ffprobe", side_effect=self._probe
        ):
            rc = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(rc, 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "not a rekordbox collection")

    def test_dry_run_writes_nothing(self) -> None:
        buf = io.StringIO()
        with patch.object(rb, "require_tools", return_value=[]), patch.object(
            rb, "run_ffprobe", side_effect=self._probe
        ), patch("sys.stdout", buf):
            rc = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                    "--dry-run",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.wav_dir.exists())
        self.assertIn("Untitled Intelligent List [WAV]", buf.getvalue())

    def test_unknown_bit_depth_and_unsupported_format(self) -> None:
        mp3 = self.music / "x.mp3"
        mp3.write_bytes(b"ID3")
        mystery = self.music / "odd.flac"
        write_flac(mystery)

        def probe(path: Path) -> dict:
            if path.suffix == ".mp3":
                return {
                    "format": {"format_name": "mp3"},
                    "streams": [
                        {
                            "codec_name": "mp3",
                            "sample_fmt": "fltp",
                            "sample_rate": "44100",
                            "channels": 2,
                        }
                    ],
                }
            if path.name == "odd.flac":
                return {
                    "format": {"format_name": "flac"},
                    "streams": [
                        {
                            "codec_name": "flac",
                            "sample_fmt": "s32",
                            "sample_rate": "44100",
                            "channels": 2,
                        }
                    ],
                }
            return self._probe(path)

        extra = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="mp3" Location="{rb.encode_location(mp3)}" Kind="MP3 File"/>
    <TRACK TrackID="2" Name="odd" Location="{rb.encode_location(mystery)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Bad" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/><TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "badfmt.xml"
        path.write_text(extra, encoding="utf-8")
        with patch.object(rb, "require_tools", return_value=[]), patch.object(
            rb, "run_ffprobe", side_effect=probe
        ):
            _, errors = rb.prepare(path, "Bad", self.wav_dir, self.output)
        joined = "\n".join(errors)
        self.assertIn("unsupported format", joined)
        self.assertIn("unknown bit depth", joined)

    def test_playlist_dir_name_sanitizes_separators(self) -> None:
        self.assertEqual(rb.playlist_dir_name("Dark forest"), "Dark forest")
        self.assertEqual(rb.playlist_dir_name("a/b\\c"), "a_b_c")
        with self.assertRaises(rb.CliError):
            rb.playlist_dir_name("..")

    def test_defaults_argparse(self) -> None:
        args = rb.parse_args(["--xml", "in.xml", "--playlist", "P"])
        self.assertEqual(args.xml, Path("in.xml"))
        self.assertEqual(args.playlist, "P")
        self.assertEqual(args.wav_dir, Path("output"))
        self.assertEqual(args.output, Path("output/rekordbox-wav-import.xml"))

    def test_optional_xml_playlist_defaults_none(self) -> None:
        args = rb.parse_args([])
        self.assertIsNone(args.xml)
        self.assertIsNone(args.playlist)


class ProgressTests(unittest.TestCase):
    def test_disabled_writes_nothing(self) -> None:
        buf = io.StringIO()
        with patch.object(rb.sys, "stderr", buf):
            bar = rb.Progress(3, enabled=False)
            bar.update(1, "convert", "a.wav")
            bar.close()
        self.assertEqual(buf.getvalue(), "")

    def test_enabled_rewrites_one_line(self) -> None:
        buf = io.StringIO()
        with patch.object(rb.sys, "stderr", buf), patch.object(
            rb.shutil, "get_terminal_size", return_value=os.terminal_size((80, 24))
        ):
            bar = rb.Progress(2, enabled=True)
            bar.update(1, "convert", "a.wav")
            bar.update(2, "skip", "b.wav")
            bar.close()
        text = buf.getvalue()
        self.assertIn("1/2", text)
        self.assertIn("convert", text)
        self.assertIn("2/2", text)
        self.assertTrue(text.endswith("\n"))


class ConvertSkipTests(unittest.TestCase):
    def test_skip_existing_valid_wav_unless_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            write_flac(src)
            wav_dir = root / "WAV"
            playlist_dir = wav_dir / "P"
            dest = playlist_dir / "a.wav"
            playlist_dir.mkdir(parents=True)
            dest.write_bytes(b"RIFF")
            el = ET.Element("TRACK", {"TrackID": "1", "Location": rb.encode_location(src)})
            item = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=rb.encode_location(dest),
                dest_name="a.wav",
                codec="pcm_s24le",
                copy_wav=False,
                noop=False,
            )
            plan = rb.Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                wav_dir=wav_dir,
                playlist_dir=playlist_dir,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
            )
            with patch.object(rb, "is_valid_pcm_wav", return_value=True), patch.object(
                rb, "run_ffmpeg"
            ) as ff:
                stats = rb.convert_unique(plan, force=False)
            ff.assert_not_called()
            self.assertEqual(stats.skipped, 1)
            with patch.object(rb, "is_valid_pcm_wav", return_value=True), patch.object(
                rb, "run_ffmpeg"
            ) as ff:
                stats = rb.convert_unique(plan, force=True)
            ff.assert_called_once()
            self.assertEqual(stats.converted, 1)


class WizardHelperTests(unittest.TestCase):
    def test_discover_xml_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            hit = cwd / "rekordbox.xml"
            hit.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            (cwd / "other.txt").write_text("x", encoding="utf-8")
            found = rb.discover_xml_candidates(
                cwd,
                candidates=(Path("rekordbox.xml"), Path("missing.xml")),
            )
            self.assertEqual(found, [hit.resolve()])

    def test_discover_xml_candidates_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                rb.discover_xml_candidates(
                    Path(tmp), candidates=(Path("rekordbox.xml"),)
                ),
                [],
            )

    def test_iter_playlists_nested_folders(self) -> None:
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="0"/>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Top" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
      <NODE Name="Intelligent playlists" Type="0" Count="1">
        <NODE Name="Nested" Type="1" KeyType="0" Entries="2">
          <TRACK Key="1"/><TRACK Key="2"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        root = ET.fromstring(xml)
        entries = rb.iter_playlists(root)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "")
        self.assertEqual(entries[0][1], "Top")
        self.assertEqual(entries[1][0], "Intelligent playlists")
        self.assertEqual(entries[1][1], "Nested")
        self.assertEqual(rb.playlist_track_count(entries[1][2]), 2)

    def test_parse_selection_single_and_multi(self) -> None:
        entries = [
            ("", "A", ET.Element("NODE")),
            ("f", "B", ET.Element("NODE")),
            ("f", "C", ET.Element("NODE")),
        ]
        chosen, errors = rb.parse_playlist_selection("1", entries)
        self.assertEqual(errors, [])
        self.assertEqual([n for _f, n, _e in chosen], ["A"])
        chosen, errors = rb.parse_playlist_selection("1,3", entries)
        self.assertEqual(errors, [])
        self.assertEqual([n for _f, n, _e in chosen], ["A", "C"])
        chosen, errors = rb.parse_playlist_selection("all", entries)
        self.assertEqual(errors, [])
        self.assertEqual(len(chosen), 3)

    def test_parse_selection_rejects_duplicate_names(self) -> None:
        entries = [
            ("one", "Same", ET.Element("NODE")),
            ("two", "Same", ET.Element("NODE")),
            ("", "Other", ET.Element("NODE")),
        ]
        chosen, errors = rb.parse_playlist_selection("1,2", entries)
        self.assertEqual(chosen, [])
        self.assertTrue(any("same name" in e for e in errors))
        chosen, errors = rb.parse_playlist_selection("all", entries)
        self.assertEqual(chosen, [])
        self.assertTrue(any("same name" in e for e in errors))

    def test_parse_selection_out_of_range(self) -> None:
        entries = [("", "A", ET.Element("NODE"))]
        _, errors = rb.parse_playlist_selection("2", entries)
        self.assertTrue(any("out of range" in e for e in errors))

    def test_main_requires_flags_when_non_tty(self) -> None:
        with patch.object(rb.sys.stdin, "isatty", return_value=False):
            rc = rb.main([])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
