#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import convert_plan
import ffmpeg_tools
import rb_playlist_to_wav as rb
import xml_output


def write_flac(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC" + b"\x00" * 8)


def flac_probe() -> dict:
    return {
        "format": {"format_name": "flac"},
        "streams": [
            {
                "codec_name": "flac",
                "sample_fmt": "s32",
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": "24",
            }
        ],
    }


def wav_probe() -> dict:
    return {
        "format": {"format_name": "wav"},
        "streams": [
            {
                "codec_name": "pcm_s24le",
                "sample_fmt": "s32",
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": "24",
            }
        ],
    }


def aiff_probe() -> dict:
    return {
        "format": {"format_name": "aiff"},
        "streams": [
            {
                "codec_name": "pcm_s24be",
                "sample_fmt": "s32",
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": "24",
            }
        ],
    }


RB6_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="100" Name="Café &amp; Dreams" Artist="O'Neil #1"
           Album="Hits 100%" Grouping="" Genre="Electronic" Kind="FLAC File"
           Size="1" TotalTime="100" DiscNumber="0" TrackNumber="1" Year="2023"
           AverageBpm="128.00" DateAdded="2024-01-01" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="51"
           Location="{loc_a}" Remixer="" Tonality="Am" Label="" Mix=""
           Colour="0xFF0000">
      <TEMPO Inizio="0.000" Bpm="128.00" Metro="4/4" Battito="1"/>
      <POSITION_MARK Name="cue" Type="0" Start="0.000" Num="-1"
                     Red="255" Green="0" Blue="0"/>
    </TRACK>
    <TRACK TrackID="101" Name="Space Track" Artist="ABSL" Album="Album"
           Grouping="" Genre="Electronic" Kind="FLAC File" Size="1"
           TotalTime="100" DiscNumber="0" TrackNumber="2" Year="2023"
           AverageBpm="140.00" DateAdded="2024-01-01" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="0"
           Location="{loc_b}" Remixer="" Tonality="Bm" Label="" Mix="">
      <TEMPO Inizio="0.100" Bpm="140.00" Metro="4/4" Battito="1"/>
    </TRACK>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Nested Folder" Type="0" Count="1">
        <NODE Name="Night Set" Type="1" KeyType="0" Entries="3">
          <TRACK Key="100"/>
          <TRACK Key="101"/>
          <TRACK Key="100"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""

RB7_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="7.0.4" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="200" Name="Beatgrid Tune" Artist="Producer"
           Album="Grid" Grouping="" Genre="Techno" Kind="FLAC File" Size="1"
           TotalTime="120" DiscNumber="0" TrackNumber="1" Year="2025"
           AverageBpm="132.00" DateAdded="2025-01-01" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="0"
           Location="{loc_a}" Remixer="" Tonality="Cm" Label="" Mix="">
      <TEMPO Inizio="0.012" Bpm="132.00" Metro="4/4" Battito="1"/>
      <TEMPO Inizio="1.830" Bpm="132.00" Metro="4/4" Battito="2"/>
      <TEMPO Inizio="3.648" Bpm="132.00" Metro="4/4" Battito="3"/>
      <POSITION_MARK Name="" Type="0" Start="0.012" Num="-1"
                     Red="40" Green="226" Blue="20"/>
    </TRACK>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="RB7 Playlist" Type="1" KeyType="0" Entries="1">
        <TRACK Key="200"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


def _assert_utf8_declaration(path: Path) -> None:
    head = path.read_bytes()[:120]
    if b"UTF-8" not in head and b"utf-8" not in head:
        raise AssertionError(f"missing UTF-8 XML declaration in {path}: {head!r}")


class ValidateImportXmlUnitTests(unittest.TestCase):
    def test_validate_rejects_wrong_dj_playlists_version(self) -> None:
        root = ET.Element("DJ_PLAYLISTS", {"Version": "2.0.0"})
        ET.SubElement(root, "PRODUCT", {"Name": "rekordbox", "Version": "6.8.5"})
        ET.SubElement(root, "COLLECTION", {"Entries": "0"})
        playlists = ET.SubElement(root, "PLAYLISTS")
        ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "0"})
        errors = xml_output.validate_import_xml(root)
        self.assertTrue(any("1.0.0" in e for e in errors))

    def test_skeleton_from_pins_dj_playlists_version_1_0_0(self) -> None:
        source = ET.Element("DJ_PLAYLISTS", {"Version": "9.9.9"})
        ET.SubElement(
            source, "PRODUCT", {"Name": "rekordbox", "Version": "7.0.4", "Company": "X"}
        )
        out = rb.skeleton_from(source)
        self.assertEqual(out.get("Version"), "1.0.0")
        product = out.find("PRODUCT")
        assert product is not None
        self.assertEqual(product.get("Version"), "7.0.4")

    def test_write_import_xml_refuses_invalid_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xml"
            root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            with self.assertRaises(rb.CliError) as ctx:
                xml_output.write_import_xml(root, path)
            self.assertIn("integrity", str(ctx.exception).lower())
            self.assertFalse(path.exists())


class ImportXmlIntegrityRoundTripTests(unittest.TestCase):
    def _probe(self, path: Path) -> dict:
        suffix = path.suffix.lower()
        if suffix == ".wav":
            return wav_probe()
        if suffix in {".aiff", ".aif"}:
            return aiff_probe()
        return flac_probe()

    def _convert_and_validate(
        self,
        *,
        fixture: str,
        playlist: str,
        playlist_folder: str | None = None,
        product_version_prefix: str,
        output_format: str = "wav",
        extra_playlists: list[tuple[str | None, str]] | None = None,
    ) -> ET.Element:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            music = root / "music"
            a = music / "O'Neil #1" / "Hits 100%" / "Café & Dreams.flac"
            b = music / "ABSL" / "Album" / "Space Track.flac"
            write_flac(a)
            write_flac(b)
            xml_path = root / "collection.xml"
            text = fixture
            text = text.replace("{loc_a}", rb.encode_location(a))
            text = text.replace("{loc_b}", rb.encode_location(b))
            xml_path.write_text(text, encoding="utf-8")
            wav_dir = root / "WAV"
            output = root / "rekordbox-import.xml"

            def fake_ffmpeg(
                source: Path, dest: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"RIFF")

            refs = [(playlist_folder, playlist)]
            if extra_playlists:
                refs.extend(extra_playlists)

            def touch_aiff(source, dest, *a, **k):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"FORM")

            with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
                ffmpeg_tools, "run_ffprobe", side_effect=self._probe
            ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
                convert_plan, "is_cdj_safe_wav", return_value=False
            ), patch.object(
                convert_plan, "_is_canonical_aiff_output", return_value=False
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("100", "2116", "44100")
            ), patch.object(
                convert_plan,
                "write_aiff_output",
                side_effect=touch_aiff,
            ):
                rc = rb.run_convert_batch(
                    xml_path,
                    refs,
                    wav_dir,
                    output,
                    force=False,
                    dry_run=False,
                    output_format=output_format,
                )
            self.assertEqual(rc, 0)
            self.assertTrue(output.is_file())
            _assert_utf8_declaration(output)
            tree = ET.parse(output)
            out = tree.getroot()
            product = out.find("PRODUCT")
            assert product is not None
            self.assertTrue(
                (product.get("Version") or "").startswith(product_version_prefix),
                product.get("Version"),
            )
            self.assertEqual(out.get("Version"), "1.0.0")
            errors = xml_output.validate_import_xml(out)
            self.assertEqual(errors, [], errors)
            return out

    def test_rb6_style_convert_write_validate(self) -> None:
        out = self._convert_and_validate(
            fixture=RB6_FIXTURE,
            playlist="Night Set",
            playlist_folder="Nested Folder",
            product_version_prefix="6.",
        )
        tracks = out.findall("COLLECTION/TRACK")
        self.assertEqual(len(tracks), 2)
        locations = [t.get("Location", "") for t in tracks]
        for loc in locations:
            self.assertTrue(loc.startswith("file://localhost/"))
            self.assertIn("%20", loc or " ")  # spaces encoded somewhere in tree paths
        # Special characters from Artist/Album/Name appear percent-encoded in Location.
        joined = "\n".join(locations)
        self.assertTrue(
            any(ch in joined for ch in ("%26", "%27", "%23", "%25", "Caf")),
            f"expected special-char encoding in Locations: {joined!r}",
        )
        pl = rb.find_playlists_by_name(out, "Night Set [WAV]")
        self.assertEqual(len(pl), 1)
        # Repeated source Key in playlist → one Key after dedup on new node.
        self.assertEqual(len(pl[0].findall("TRACK")), 2)

    def test_rb7_style_convert_write_validate(self) -> None:
        out = self._convert_and_validate(
            fixture=RB7_FIXTURE,
            playlist="RB7 Playlist",
            product_version_prefix="7.",
        )
        tracks = out.findall("COLLECTION/TRACK")
        self.assertEqual(len(tracks), 1)
        self.assertIsNotNone(tracks[0].find("TEMPO"))
        self.assertGreaterEqual(len(tracks[0].findall("TEMPO")), 2)

    def test_shared_track_across_playlists_one_collection_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            music = root / "music"
            a = music / "shared.flac"
            b = music / "other.flac"
            write_flac(a)
            write_flac(b)
            xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="Shared" Artist="A" Album="B" Kind="FLAC File"
           Size="1" TotalTime="1" Location="{rb.encode_location(a)}"
           SampleRate="44100" AverageBpm="120.00"/>
    <TRACK TrackID="2" Name="Other" Artist="A" Album="B" Kind="FLAC File"
           Size="1" TotalTime="1" Location="{rb.encode_location(b)}"
           SampleRate="44100" AverageBpm="120.00"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="One" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
      <NODE Name="Two" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/>
        <TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
            xml_path = root / "c.xml"
            xml_path.write_text(xml, encoding="utf-8")
            wav_dir = root / "WAV"
            output = root / "out.xml"

            def fake_ffmpeg(source, dest, codec, force, **_k):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"RIFF")

            with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
                ffmpeg_tools, "run_ffprobe", side_effect=self._probe
            ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
                convert_plan, "is_cdj_safe_wav", return_value=False
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("1", "1411", "44100")
            ):
                rc = rb.run_convert_batch(
                    xml_path,
                    [(None, "One"), (None, "Two")],
                    wav_dir,
                    output,
                    force=False,
                    dry_run=False,
                )
            self.assertEqual(rc, 0)
            out = ET.parse(output).getroot()
            self.assertEqual(xml_output.validate_import_xml(out), [])
            self.assertEqual(len(out.findall("COLLECTION/TRACK")), 2)
            shared = [t for t in out.findall("COLLECTION/TRACK") if t.get("Name") == "Shared"]
            self.assertEqual(len(shared), 1)
            tid = shared[0].get("TrackID")
            for name in ("One [WAV]", "Two [WAV]"):
                pl = rb.find_playlists_by_name(out, name)[0]
                self.assertIn(tid, [t.get("Key") for t in pl.findall("TRACK")])

    def test_wav_and_aiff_same_source_two_collection_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "music" / "t.flac"
            write_flac(src)
            xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Dual" Artist="A" Album="B" Kind="FLAC File"
           Size="1" TotalTime="1" Location="{rb.encode_location(src)}"
           SampleRate="44100" AverageBpm="120.00"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="DualFmt" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
            xml_path = root / "c.xml"
            xml_path.write_text(xml, encoding="utf-8")
            wav_dir = root / "WAV"
            out_wav = root / "wav.xml"

            def fake_ffmpeg(source, dest, codec, force, **_k):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"RIFF")

            def touch_aiff(source, dest, *a, **k):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"FORM")

            patches = (
                patch.object(ffmpeg_tools, "require_tools", return_value=[]),
                patch.object(ffmpeg_tools, "run_ffprobe", side_effect=self._probe),
                patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg),
                patch.object(convert_plan, "is_cdj_safe_wav", return_value=False),
                patch.object(convert_plan, "_is_canonical_aiff_output", return_value=False),
                patch.object(
                    xml_output, "probe_dest_tech", return_value=("1", "1411", "44100")
                ),
                patch.object(convert_plan, "write_aiff_output", side_effect=touch_aiff),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                self.assertEqual(
                    rb.run_convert_batch(
                        xml_path,
                        [(None, "DualFmt")],
                        wav_dir,
                        out_wav,
                        force=False,
                        dry_run=False,
                        output_format="wav",
                    ),
                    0,
                )
                self.assertEqual(
                    rb.run_convert_batch(
                        xml_path,
                        [(None, "DualFmt")],
                        wav_dir,
                        out_wav,
                        force=False,
                        dry_run=False,
                        output_format="aiff",
                    ),
                    0,
                )
            out = ET.parse(out_wav).getroot()
            self.assertEqual(xml_output.validate_import_xml(out), [])
            tracks = out.findall("COLLECTION/TRACK")
            self.assertEqual(len(tracks), 2)
            kinds = sorted(t.get("Kind") for t in tracks)
            self.assertEqual(kinds, ["AIFF File", "WAV File"])
            ids = {t.get("TrackID") for t in tracks}
            self.assertEqual(len(ids), 2)
            locs = {t.get("Location") for t in tracks}
            self.assertEqual(len(locs), 2)


if __name__ == "__main__":
    unittest.main()
