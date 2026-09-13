#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unicodedata
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_SRC, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import rb_playlist_to_wav as rb
from cli_error import CancelledError, CliError
import cdj_wav
import convert.paths
import convert.plan
import converter_manifest
import ffmpeg_tools
from convert_fixtures import (
    FIXTURE,
    XmlFixtureTests as XmlFixtureBase,
    flac_probe,
    wav_probe,
    write_flac,
    write_pcm_wav,
)


class XmlFixtureTests(XmlFixtureBase):
    def test_identical_metadata_gets_numbered_suffixes(self) -> None:
        """Given three sources with identical Artist/Album/Name: When prepare
        runs: Then dests are Name.wav, Name (2).wav, Name (3).wav and none are
        dropped for the clash."""
        srcs = [
            self.music / "a" / "one.flac",
            self.music / "b" / "two.flac",
            self.music / "c" / "three.flac",
        ]
        for p in srcs:
            write_flac(p)
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="1" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(srcs[0])}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(srcs[1])}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(srcs[2])}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Triple" Type="1" KeyType="0" Entries="3">
        <TRACK Key="1"/><TRACK Key="2"/><TRACK Key="3"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "triple-clash.xml"
        path.write_text(xml, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(path, "Triple", self.wav_dir, self.output)
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 3)
        dests = {
            u.source_path: u.dest_path.relative_to(plan.library_dir).as_posix()
            for u in plan.unique
        }
        self.assertEqual(dests[srcs[0]], "WAV/Same - Song.wav")
        self.assertEqual(dests[srcs[1]], "WAV/Same - Song (2).wav")
        self.assertEqual(dests[srcs[2]], "WAV/Same - Song (3).wav")

    def test_numbered_dest_stays_stable_when_earlier_source_absent(
        self,
    ) -> None:
        """Given Song.wav and Song (2).wav reserved: When only the (2) source
        remains in the playlist: Then it keeps Song (2).wav (no compact)."""
        first = self.music / "a" / "one.flac"
        second = self.music / "b" / "two.flac"
        for p in (first, second):
            write_flac(p)

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        both = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(first)}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(second)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Both" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/><TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "stable-number.xml"
        path.write_text(both, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            rc1 = rb.main(
                [
                    "--xml",
                    str(path),
                    "--playlist",
                    "Both",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(rc1, 0)
        data = json.loads(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert.paths.source_key(second)]["wav"]["dest"],
            "WAV/Same - Song (2).wav",
        )

        only_second = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="2" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(second)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="OnlySecond" Type="1" KeyType="0" Entries="1">
        <TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path.write_text(only_second, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=True
        ):
            plan, errors = rb.prepare(
                path, "OnlySecond", self.wav_dir, self.output
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 1)
        self.assertEqual(
            plan.unique[0].dest_path.relative_to(plan.library_dir).as_posix(),
            "WAV/Same - Song (2).wav",
        )
        data2 = json.loads(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data2["tracks"][convert.paths.source_key(second)]["wav"]["dest"],
            "WAV/Same - Song (2).wav",
        )
        self.assertNotEqual(
            plan.unique[0].dest_path.relative_to(plan.library_dir).as_posix(),
            "WAV/Same - Song.wav",
        )

    def test_unrelated_occupant_forces_numbered_dest_on_first_assign(
        self,
    ) -> None:
        """Given a file already at preferred dest but not in the manifest: When
        convert assigns that source: Then dest is Name (2).ext and the occupant
        file is left intact."""
        src = self.music / "solo" / "track.flac"
        write_flac(src)
        preferred = self.wav_dir / "WAV" / "Same - Song.wav"
        preferred.parent.mkdir(parents=True)
        preferred.write_bytes(b"UNRELATED-OCCUPANT")
        # Managed library (valid empty manifest) so orphan audio is allowed.
        (self.wav_dir / converter_manifest.MANIFEST_NAME).write_text(
            json.dumps({"version": 1, "layout": "format-flat", "tracks": {}}),
            encoding="utf-8",
        )

        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(src)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Occupy" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "unrelated-occupant.xml"
        path.write_text(xml, encoding="utf-8")

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            rc = rb.main(
                [
                    "--xml",
                    str(path),
                    "--playlist",
                    "Occupy",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(rc, 0)
        numbered = self.wav_dir / "WAV" / "Same - Song (2).wav"
        self.assertTrue(numbered.is_file())
        self.assertEqual(preferred.read_bytes(), b"UNRELATED-OCCUPANT")
        data = json.loads(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert.paths.source_key(src)]["wav"]["dest"],
            "WAV/Same - Song (2).wav",
        )

    def test_collisions_case_and_nfd(self) -> None:
        """Given case and NFD dest twins with different sources: When prepare
        runs: Then each source gets a sticky numbered dest (Name, Name (2), …)
        and unique keeps every source."""
        intro = self.music / "one" / "Intro.flac"
        intro2 = self.music / "two" / "intro.flac"
        cafe_nfc = self.music / "n1" / (unicodedata.normalize("NFC", "café") + ".flac")
        cafe_nfd = self.music / "n2" / (unicodedata.normalize("NFD", "café") + ".flac")
        for p in (intro, intro2, cafe_nfc, cafe_nfd):
            write_flac(p)
        cafe_name_nfc = unicodedata.normalize("NFC", "café")
        cafe_name_nfd = unicodedata.normalize("NFD", "café")
        extra = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="4">
    <TRACK TrackID="1" Name="Intro" Artist="Same" Album="Hits"
           Location="{rb.encode_location(intro)}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="intro" Artist="Same" Album="Hits"
           Location="{rb.encode_location(intro2)}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="{cafe_name_nfc}" Artist="Same" Album="Hits"
           Location="{rb.encode_location(cafe_nfc)}" Kind="FLAC File"/>
    <TRACK TrackID="4" Name="{cafe_name_nfd}" Artist="Same" Album="Hits"
           Location="{rb.encode_location(cafe_nfd)}" Kind="FLAC File"/>
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
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(path, "Clash", self.wav_dir, self.output)
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 4)
        intro_dests = sorted(
            u.dest_path.relative_to(plan.library_dir).as_posix()
            for u in plan.unique
            if "intro" in u.dest_name.casefold()
        )
        self.assertEqual(len(intro_dests), 2)
        self.assertEqual(
            {rb.collision_key(d) for d in intro_dests},
            {
                rb.collision_key("WAV/Same - Intro.wav"),
                rb.collision_key("WAV/Same - Intro (2).wav"),
            },
        )
        first_intro = next(u for u in plan.unique if u.source_path == intro)
        self.assertEqual(
            first_intro.dest_path.relative_to(plan.library_dir).as_posix(),
            "WAV/Same - Intro.wav",
        )
        cafe_dests = sorted(
            u.dest_path.relative_to(plan.library_dir).as_posix()
            for u in plan.unique
            if "caf" in unicodedata.normalize("NFC", u.dest_name).casefold()
        )
        self.assertEqual(len(cafe_dests), 2)
        self.assertTrue(any(" (2).wav" in d for d in cafe_dests))
        self.assertTrue(
            any(
                rb.collision_key(d) == rb.collision_key("WAV/Same - café.wav")
                for d in cafe_dests
            )
        )

    def test_sticky_manifest_reuses_dest_when_metadata_changes(self) -> None:
        """Given an existing wav assignment: When Artist/Album/Name change and
        convert reruns: Then the sticky dest path is reused (not Preferred) and
        import XML metadata is refreshed at that Location."""
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            rc1 = rb.main(
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
        self.assertEqual(rc1, 0)
        sticky = "WAV/ABSL - Bestial.wav"
        sticky_path = self.wav_dir / Path(sticky)
        self.assertTrue(sticky_path.is_file())
        root = rb.load_dj_playlists(self.xml_path)
        track = root.find("COLLECTION/TRACK")
        assert track is not None
        track.set("Artist", "New Artist")
        track.set("Album", "New Album")
        track.set("Name", "New Name")
        ET.ElementTree(root).write(self.xml_path, encoding="UTF-8", xml_declaration=True)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=True
        ):
            rc2 = rb.main(
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
        self.assertEqual(rc2, 0)
        self.assertTrue(sticky_path.is_file())
        self.assertFalse(
            (self.wav_dir / "WAV" / "New Artist - New Name.wav").exists()
        )
        data = json.loads(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert.paths.source_key(self.a)]["wav"]["dest"],
            sticky,
        )
        out = ET.parse(self.output).getroot()
        sticky_loc = rb.encode_location(sticky_path)
        refreshed = next(
            t for t in out.findall("COLLECTION/TRACK") if t.get("Location") == sticky_loc
        )
        self.assertEqual(refreshed.get("Name"), "New Name")
        self.assertEqual(refreshed.get("Artist"), "New Artist")
        self.assertEqual(refreshed.get("Album"), "New Album")

    def test_failed_encodes_keep_manifest_reservations(self) -> None:
        """Given encode failures: When main returns nonzero: Then the manifest
        still retains the reserved dests from before conversion."""
        def boom(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            raise CliError(f"boom {source.name}")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=boom), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
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
        data = json.loads(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert.paths.source_key(self.a)]["wav"]["dest"],
            "WAV/ABSL - Bestial.wav",
        )

    def test_missing_dest_recreates_at_same_sticky_path(self) -> None:
        """Given a reserved sticky dest whose file was deleted: When convert
        reruns: Then the file is recreated at the same path (no Name (2))."""
        sticky = "WAV/ABSL - Bestial.wav"
        wrote: list[Path] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")
            wrote.append(dest)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            rc1 = rb.main(
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
        self.assertEqual(rc1, 0)
        dest = self.wav_dir / Path(sticky)
        self.assertTrue(dest.is_file())
        dest.unlink()
        self.assertFalse(dest.exists())
        wrote.clear()

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            rc2 = rb.main(
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
        self.assertEqual(rc2, 0)
        self.assertTrue(dest.is_file())
        self.assertIn(dest, wrote)
        self.assertFalse(
            (self.wav_dir / "WAV" / "ABSL - Bestial (2).wav").exists()
        )
        data = json.loads(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert.paths.source_key(self.a)]["wav"]["dest"],
            sticky,
        )

    def test_effective_quality_mismatch_rebuilds_in_place_sticky_path(self) -> None:
        """Given a reduced dest for a higher-quality source: When the ceiling
        rises so effective depth/rate no longer matches: Then replace in place
        at the sticky path (no Name (2))."""
        sticky = "WAV/ABSL - Bestial.wav"
        converted: list[Path] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            bits = kwargs.get("bit_depth", 16)
            rate = kwargs.get("sample_rate", 44100)
            write_pcm_wav(dest, bits=bits, sample_rate=rate)
            converted.append(dest)

        def probe24(path: Path, **_kwargs: object) -> dict:
            if path.suffix.lower() == ".wav":
                return wav_probe(bits=24)
            return flac_probe(bits=24)

        args_low = [
            "--xml",
            str(self.xml_path),
            "--playlist",
            "Untitled Intelligent List",
            "--wav-dir",
            str(self.wav_dir),
            "--output",
            str(self.output),
            "--bit-depth",
            "16",
            "--sample-rate",
            "44100",
        ]
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe24
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
            self.assertEqual(rb.main(args_low), 0)
        dest = self.wav_dir / Path(sticky)
        self.assertTrue(
            cdj_wav.is_cdj_safe_wav(dest, bit_depth=16, sample_rate=44100)
        )
        converted.clear()

        args_high = [
            "--xml",
            str(self.xml_path),
            "--playlist",
            "Untitled Intelligent List",
            "--wav-dir",
            str(self.wav_dir),
            "--output",
            str(self.output),
            "--bit-depth",
            "24",
            "--sample-rate",
            "48000",
        ]
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe24
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
            self.assertEqual(rb.main(args_high), 0)
        self.assertIn(dest, converted)
        self.assertTrue(
            cdj_wav.is_cdj_safe_wav(dest, bit_depth=24, sample_rate=44100)
        )
        self.assertFalse(
            (self.wav_dir / "WAV" / "ABSL - Bestial (2).wav").exists()
        )
        data = json.loads(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert.paths.source_key(self.a)]["wav"]["dest"],
            sticky,
        )

    def test_wav_and_aiff_assignments_coexist_in_manifest(self) -> None:
        """Given the same source converted as wav then aiff: When manifests are
        saved: Then both format dests coexist under one source_key."""
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        def fake_aiff(
            source: Path,
            dest: Path,
            source_el,
            **_kwargs,
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"FORM")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ), patch.object(convert.plan, "write_aiff_output", side_effect=fake_aiff):
            rc_wav = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                    "--format",
                    "wav",
                ]
            )
            rc_aiff = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.root / "out-aiff.xml"),
                    "--format",
                    "aiff",
                ]
            )
        self.assertEqual(rc_wav, 0)
        self.assertEqual(rc_aiff, 0)
        data = json.loads(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        entry = data["tracks"][convert.paths.source_key(self.a)]
        self.assertEqual(
            entry["wav"]["dest"],
            "WAV/ABSL - Bestial.wav",
        )
        self.assertEqual(
            entry["aiff"]["dest"],
            "AIFF/ABSL - Bestial.aiff",
        )

    def test_unknown_fields_preserved_and_ids_start_at_1(self) -> None:
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
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
        self.assertIn("/WAV/ABSL%20-%20Bestial.wav", loc)
        self.assertTrue(
            (self.wav_dir / "WAV" / "ABSL - Bestial.wav").is_file()
        )
        extra = first.find("EXTRA")
        self.assertIsNotNone(extra)
        assert extra is not None
        self.assertEqual(extra.get("Foo"), "bar")
        self.assertIsNotNone(first.find("TEMPO"))
        self.assertIsNotNone(first.find("POSITION_MARK"))
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")
        self.assertEqual(len(pl), 1)
        self.assertEqual([t.get("Key") for t in pl[0].findall("TRACK")], ["1", "2", "3"])



if __name__ == "__main__":
    unittest.main()
