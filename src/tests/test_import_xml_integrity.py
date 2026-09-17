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

import ffmpeg_tools
from rekordbox_xml import encode_location, find_playlists_by_name, skeleton_from
from cli_error import CliError
import convert.plan
import xml_output
import rb_playlist_to_wav as rb
from convert.models import PlannedTrack


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


RB6_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="100" Name="Café &amp; Dreams" Artist="O'Neil #1"
           Composer="Jane Doe" Album="Hits 100%" Grouping="Prep"
           Genre="Electronic" Kind="FLAC File" Size="1" TotalTime="100"
           DiscNumber="1" TrackNumber="1" Year="2023" AverageBpm="128.00"
           DateAdded="2024-01-01" BitRate="0" SampleRate="44100"
           Comments="prep notes" PlayCount="19" Rating="51"
           Location="{loc_a}" Remixer="DJ X" Tonality="Am"
           Label="Mama told ya" Mix="Original" Colour="0xFF0000"
           UnknownAttr="keep-me">
      <TEMPO Inizio="0.000" Bpm="128.00" Metro="4/4" Battito="1"/>
      <TEMPO Inizio="32.000" Bpm="129.00" Metro="4/4" Battito="1"/>
      <POSITION_MARK Name="cue" Type="0" Start="0.000" Num="-1"
                     Red="255" Green="0" Blue="0"/>
      <POSITION_MARK Name="hot" Type="0" Start="4.000" Num="0"
                     Red="0" Green="255" Blue="0"/>
      <POSITION_MARK Name="loop" Type="4" Start="8.000" End="16.000" Num="-1"
                     Red="0" Green="0" Blue="255"/>
      <EXTRA Foo="bar"/>
    </TRACK>
    <TRACK TrackID="101" Name="Space Track" Artist="ABSL" Album="Album"
           Composer="" Grouping="" Genre="Electronic" Kind="FLAC File"
           Size="1" TotalTime="100" DiscNumber="0" TrackNumber="2"
           Year="2023" AverageBpm="140.00" DateAdded="2024-01-01"
           BitRate="0" SampleRate="44100" Comments="" PlayCount="0"
           Rating="0" Location="{loc_b}" Remixer="" Tonality="Bm"
           Label="" Mix="">
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


RB7_FIXTURE = RB6_FIXTURE.replace(
    'PRODUCT Name="rekordbox" Version="6.8.5"',
    'PRODUCT Name="rekordbox" Version="7.0.4"',
    1,
)


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
        out = skeleton_from(source)
        self.assertEqual(out.get("Version"), "1.0.0")
        product = out.find("PRODUCT")
        assert product is not None
        self.assertEqual(product.get("Version"), "7.0.4")

    def test_write_import_xml_refuses_invalid_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xml"
            root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            with self.assertRaises(CliError) as ctx:
                xml_output.write_import_xml(root, path)
            self.assertIn("integrity", str(ctx.exception).lower())
            self.assertFalse(path.exists())


class ImportXmlIntegrityRoundTripTests(unittest.TestCase):
    def _probe(self, path: Path, **_kwargs: object) -> dict:
        if path.suffix.lower() == ".wav":
            return wav_probe()
        return flac_probe()

    def _convert_and_validate(
        self,
        *,
        fixture: str | None,
        playlist: str,
        playlist_folder: str | None = None,
        product_version_prefix: str,
        output_format: str = "wav",
        root: Path | None = None,
        setup: bool = True,
        encode_calls: list[Path] | None = None,
    ) -> ET.Element:
        def run(workspace: Path) -> ET.Element:
            music = workspace / "music"
            a = music / "O'Neil #1" / "Hits 100%" / "Café & Dreams.flac"
            b = music / "ABSL" / "Album" / "Space Track.flac"
            xml_path = workspace / "collection.xml"
            if setup:
                if fixture is None:
                    raise AssertionError("setup=True requires fixture text")
                write_flac(a)
                write_flac(b)
                text = fixture
                text = text.replace("{loc_a}", encode_location(a))
                text = text.replace("{loc_b}", encode_location(b))
                xml_path.write_text(text, encoding="utf-8")
            wav_dir = workspace / output_format.upper()
            output = workspace / "rekordbox-import.xml"
            calls = encode_calls if encode_calls is not None else []

            def fake_ffmpeg(
                source: Path, dest: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                calls.append(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"RIFF")

            def fake_aiff(source, dest, *args, **_kwargs) -> None:
                calls.append(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"FORM")

            with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
                ffmpeg_tools, "run_ffprobe", side_effect=self._probe
            ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
                convert.plan, "write_aiff_output", side_effect=fake_aiff
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("100", "2116", "44100")
            ):
                rc = rb.run_convert_batch(
                    xml_path,
                    [(playlist_folder, playlist)],
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

        if root is not None:
            return run(root)
        with tempfile.TemporaryDirectory() as tmp:
            return run(Path(tmp))

    def test_rb_style_convert_preserves_preparation_metadata(self) -> None:
        """DDD-146: RB6/RB7 × WAV/AIFF round-trip keeps preparation metadata,
        cues, constant and variable TEMPO grids, flat playlists, and repeated
        Keys; Import XML validates after every convert."""
        cases = (
            (RB6_FIXTURE, "6.", "wav"),
            (RB7_FIXTURE, "7.", "wav"),
            (RB6_FIXTURE, "6.", "aiff"),
            (RB7_FIXTURE, "7.", "aiff"),
        )
        for fixture, version_prefix, output_format in cases:
            with self.subTest(version=version_prefix, fmt=output_format):
                out = self._convert_and_validate(
                    fixture=fixture,
                    playlist="Night Set",
                    playlist_folder="Nested Folder",
                    product_version_prefix=version_prefix,
                    output_format=output_format,
                )
                kind = "AIFF File" if output_format == "aiff" else "WAV File"
                suffix = "AIFF" if output_format == "aiff" else "WAV"
                tracks = out.findall("COLLECTION/TRACK")
                self.assertEqual(len(tracks), 2)
                self.assertEqual({t.get("Kind") for t in tracks}, {kind})
                locations = [t.get("Location", "") for t in tracks]
                for loc in locations:
                    self.assertTrue(loc.startswith("file://localhost/"))
                    self.assertIn("%20", loc or " ")
                joined = "\n".join(locations)
                self.assertTrue(
                    any(ch in joined for ch in ("%26", "%27", "%23", "%25", "Caf")),
                    f"expected special-char encoding in Locations: {joined!r}",
                )
                pl = find_playlists_by_name(out, f"Night Set [{suffix}]")
                self.assertEqual(len(pl), 1)
                playlists = out.find("PLAYLISTS")
                assert playlists is not None
                folder_names = [
                    n.get("Name")
                    for n in playlists.iter("NODE")
                    if n.get("Type") == "0" and n.get("Name") != "ROOT"
                ]
                self.assertNotIn("Nested Folder", folder_names)
                cafe = next(t for t in tracks if t.get("Name") == "Café & Dreams")
                space = next(t for t in tracks if t.get("Name") == "Space Track")
                keys = [t.get("Key") for t in pl[0].findall("TRACK")]
                self.assertEqual(
                    keys,
                    [cafe.get("TrackID"), space.get("TrackID"), cafe.get("TrackID")],
                )
                self.assertEqual(cafe.get("Artist"), "O'Neil #1")
                self.assertEqual(cafe.get("Composer"), "Jane Doe")
                self.assertEqual(cafe.get("Album"), "Hits 100%")
                self.assertEqual(cafe.get("Grouping"), "Prep")
                self.assertEqual(cafe.get("Genre"), "Electronic")
                self.assertEqual(cafe.get("TotalTime"), "100")
                self.assertEqual(cafe.get("DiscNumber"), "1")
                self.assertEqual(cafe.get("TrackNumber"), "1")
                self.assertEqual(cafe.get("Year"), "2023")
                self.assertEqual(cafe.get("AverageBpm"), "128.00")
                self.assertEqual(cafe.get("DateAdded"), "2024-01-01")
                self.assertEqual(cafe.get("Comments"), "prep notes")
                self.assertEqual(cafe.get("PlayCount"), "19")
                self.assertEqual(cafe.get("Rating"), "51")
                self.assertEqual(cafe.get("Remixer"), "DJ X")
                self.assertEqual(cafe.get("Tonality"), "Am")
                self.assertEqual(cafe.get("Label"), "Mama told ya")
                self.assertEqual(cafe.get("Mix"), "Original")
                self.assertEqual(cafe.get("Colour"), "0xFF0000")
                self.assertEqual(cafe.get("UnknownAttr"), "keep-me")
                extra = cafe.find("EXTRA")
                assert extra is not None
                self.assertEqual(extra.get("Foo"), "bar")
                tempos = cafe.findall("TEMPO")
                self.assertEqual(len(tempos), 2)
                self.assertEqual(tempos[0].get("Inizio"), "0.000")
                self.assertEqual(tempos[0].get("Bpm"), "128.00")
                self.assertEqual(tempos[0].get("Metro"), "4/4")
                self.assertEqual(tempos[0].get("Battito"), "1")
                self.assertEqual(tempos[1].get("Inizio"), "32.000")
                self.assertEqual(tempos[1].get("Bpm"), "129.00")
                self.assertEqual(tempos[1].get("Metro"), "4/4")
                self.assertEqual(tempos[1].get("Battito"), "1")
                space_tempos = space.findall("TEMPO")
                self.assertEqual(len(space_tempos), 1)
                self.assertEqual(space_tempos[0].get("Inizio"), "0.100")
                self.assertEqual(space_tempos[0].get("Bpm"), "140.00")
                self.assertEqual(space_tempos[0].get("Metro"), "4/4")
                self.assertEqual(space_tempos[0].get("Battito"), "1")
                marks = cafe.findall("POSITION_MARK")
                self.assertEqual(len(marks), 3)
                by_num = {(m.get("Type"), m.get("Num"), m.get("Name")): m for m in marks}
                cue = by_num[("0", "-1", "cue")]
                self.assertEqual(cue.get("Start"), "0.000")
                self.assertEqual(cue.get("Red"), "255")
                self.assertEqual(cue.get("Green"), "0")
                self.assertEqual(cue.get("Blue"), "0")
                hot = by_num[("0", "0", "hot")]
                self.assertEqual(hot.get("Start"), "4.000")
                self.assertEqual(hot.get("Red"), "0")
                self.assertEqual(hot.get("Green"), "255")
                self.assertEqual(hot.get("Blue"), "0")
                loop = by_num[("4", "-1", "loop")]
                self.assertEqual(loop.get("Start"), "8.000")
                self.assertEqual(loop.get("End"), "16.000")
                self.assertEqual(loop.get("Red"), "0")
                self.assertEqual(loop.get("Green"), "0")
                self.assertEqual(loop.get("Blue"), "255")
                coll = out.find("COLLECTION")
                assert coll is not None
                self.assertEqual(coll.get("Entries"), "2")
                self.assertEqual(pl[0].get("Entries"), "3")

    def test_metadata_refresh_updates_cues_keeps_track_id(self) -> None:
        """DDD-146: Given an existing generated collection row: When source
        cues/TEMPO/colour/comments/rating change and convert reruns: Then
        Import XML refreshes those fields, TrackID and playlist Keys stay,
        unknown extras remain, validate passes, and ffmpeg is not called."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            encode_calls: list[Path] = []
            out1 = self._convert_and_validate(
                fixture=RB6_FIXTURE,
                playlist="Night Set",
                playlist_folder="Nested Folder",
                product_version_prefix="6.",
                output_format="wav",
                root=root,
                encode_calls=encode_calls,
            )
            self.assertEqual(len(encode_calls), 2)
            cafe1 = next(
                t
                for t in out1.findall("COLLECTION/TRACK")
                if t.get("Name") == "Café & Dreams"
            )
            space1 = next(
                t
                for t in out1.findall("COLLECTION/TRACK")
                if t.get("Name") == "Space Track"
            )
            pl1 = find_playlists_by_name(out1, "Night Set [WAV]")[0]
            keys1 = [t.get("Key") for t in pl1.findall("TRACK")]
            cafe_tid = cafe1.get("TrackID")
            space_tid = space1.get("TrackID")
            cafe_loc = cafe1.get("Location")

            xml_path = root / "collection.xml"
            source = ET.parse(xml_path).getroot()
            cafe_src = next(
                t
                for t in source.findall("COLLECTION/TRACK")
                if t.get("Name") == "Café & Dreams"
            )
            cafe_src.set("Comments", "updated notes")
            cafe_src.set("Colour", "0x00FF00")
            cafe_src.set("Rating", "204")
            tempos = cafe_src.findall("TEMPO")
            tempos[0].set("Bpm", "130.00")
            cue = next(
                m for m in cafe_src.findall("POSITION_MARK") if m.get("Name") == "cue"
            )
            cue.set("Name", "intro")
            cue.set("Start", "1.500")
            ET.ElementTree(source).write(
                xml_path, encoding="UTF-8", xml_declaration=True
            )

            first_encodes = list(encode_calls)
            out2 = self._convert_and_validate(
                fixture=None,
                playlist="Night Set",
                playlist_folder="Nested Folder",
                product_version_prefix="6.",
                output_format="wav",
                root=root,
                setup=False,
                encode_calls=encode_calls,
            )
            self.assertEqual(encode_calls, first_encodes)

            cafe2 = next(
                t
                for t in out2.findall("COLLECTION/TRACK")
                if t.get("Location") == cafe_loc
            )
            self.assertEqual(cafe2.get("TrackID"), cafe_tid)
            self.assertEqual(cafe2.get("Comments"), "updated notes")
            self.assertEqual(cafe2.get("Colour"), "0x00FF00")
            self.assertEqual(cafe2.get("Rating"), "204")
            self.assertEqual(cafe2.get("UnknownAttr"), "keep-me")
            extra = cafe2.find("EXTRA")
            assert extra is not None
            self.assertEqual(extra.get("Foo"), "bar")
            tempos2 = cafe2.findall("TEMPO")
            self.assertEqual(len(tempos2), 2)
            self.assertEqual(tempos2[0].get("Bpm"), "130.00")
            self.assertEqual(tempos2[0].get("Inizio"), "0.000")
            marks = cafe2.findall("POSITION_MARK")
            by_num = {(m.get("Type"), m.get("Num"), m.get("Name")): m for m in marks}
            intro = by_num[("0", "-1", "intro")]
            self.assertEqual(intro.get("Start"), "1.500")
            self.assertEqual(intro.get("Red"), "255")
            self.assertIn(("0", "0", "hot"), by_num)
            self.assertIn(("4", "-1", "loop"), by_num)
            pl2 = find_playlists_by_name(out2, "Night Set [WAV]")[0]
            keys2 = [t.get("Key") for t in pl2.findall("TRACK")]
            self.assertEqual(keys2, keys1)
            self.assertEqual(keys2, [cafe_tid, space_tid, cafe_tid])
            self.assertEqual(xml_output.validate_import_xml(out2), [])

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
           Size="1" TotalTime="1" Location="{encode_location(a)}"
           SampleRate="44100" AverageBpm="120.00"/>
    <TRACK TrackID="2" Name="Other" Artist="A" Album="B" Kind="FLAC File"
           Size="1" TotalTime="1" Location="{encode_location(b)}"
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
            ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
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
                pl = find_playlists_by_name(out, name)[0]
                self.assertIn(tid, [t.get("Key") for t in pl.findall("TRACK")])


class OutputFormatSourceOfTruthTests(unittest.TestCase):
    """Kind / assignment_key follow PlannedTrack.output_format, not dest suffix."""

    def test_assignment_key_follows_output_format_not_dest_suffix(self) -> None:
        """Given dest .wav but output_format aiff: assignment_key is aiff."""
        dest = Path("/out/WAV/Artist - Track.wav")
        item = PlannedTrack(
            source_el=ET.Element("TRACK", {"Name": "Track"}),
            source_path=Path("/music/track.flac"),
            dest_path=dest,
            dest_location=encode_location(dest),
            dest_name=dest.name,
            codec="pcm_s24be",
            passthrough=False,
            noop=False,
            output_format="aiff",
        )
        _, fmt = xml_output.assignment_key(item)
        self.assertEqual(fmt, "aiff")

    def test_assignment_key_coerces_output_format(self) -> None:
        """Given dest .wav but output_format AIFF: assignment_key still aiff."""
        dest = Path("/out/WAV/Artist - Track.wav")
        item = PlannedTrack(
            source_el=ET.Element("TRACK", {"Name": "Track"}),
            source_path=Path("/music/track.flac"),
            dest_path=dest,
            dest_location=encode_location(dest),
            dest_name=dest.name,
            codec="pcm_s24be",
            passthrough=False,
            noop=False,
            output_format="AIFF",
        )
        _, fmt = xml_output.assignment_key(item)
        self.assertEqual(fmt, "aiff")

    def test_clone_track_kind_follows_output_format_not_dest_suffix(self) -> None:
        """Given dest .wav but output_format aiff: Kind is AIFF File."""
        dest = Path("/out/WAV/Artist - Track.wav")
        el = ET.Element("TRACK", {"Name": "Track", "Artist": "Artist"})
        with patch.object(
            xml_output, "probe_dest_tech", return_value=("1", "1411", "44100")
        ):
            clone = xml_output.clone_track(
                el,
                "1",
                dest,
                encode_location(dest),
                output_format="aiff",
            )
        self.assertEqual(clone.get("Kind"), "AIFF File")


if __name__ == "__main__":
    unittest.main()
