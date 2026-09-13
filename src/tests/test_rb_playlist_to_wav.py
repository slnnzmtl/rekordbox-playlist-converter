#!/usr/bin/env python3
from __future__ import annotations

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
import ffmpeg_tools
from convert.paths import collision_key
from rekordbox_xml import find_playlists_by_name, load_dj_playlists
from convert_fixtures import XmlFixtureTests as XmlFixtureBase
from convert_fixtures import FIXTURE, flac_probe, wav_probe, write_flac


class CodecMapTests(unittest.TestCase):
    def test_16_24_32_float(self) -> None:
        self.assertEqual(
            ffmpeg_tools.pcm_codec_for_stream({"sample_fmt": "s16", "bits_per_raw_sample": "16"}),
            "pcm_s16le",
        )
        self.assertEqual(
            ffmpeg_tools.pcm_codec_for_stream({"sample_fmt": "s32", "bits_per_raw_sample": "24"}),
            "pcm_s24le",
        )
        self.assertEqual(
            ffmpeg_tools.pcm_codec_for_stream({"sample_fmt": "s32", "bits_per_raw_sample": "32"}),
            "pcm_s32le",
        )
        self.assertEqual(
            ffmpeg_tools.pcm_codec_for_stream({"sample_fmt": "fltp"}),
            "pcm_f32le",
        )

    def test_unknown_depth_fails(self) -> None:
        with self.assertRaises(CliError):
            ffmpeg_tools.pcm_codec_for_stream({"sample_fmt": "s32"})
        with self.assertRaises(CliError):
            ffmpeg_tools.pcm_codec_for_stream({"sample_fmt": "u8", "bits_per_raw_sample": "8"})


class CollisionKeyTests(unittest.TestCase):
    def test_case_and_nfd(self) -> None:
        self.assertEqual(collision_key("Intro.wav"), collision_key("intro.wav"))
        nfc = unicodedata.normalize("NFC", "café.wav")
        nfd = unicodedata.normalize("NFD", "café.wav")
        self.assertNotEqual(nfc, nfd)
        self.assertEqual(collision_key(nfc), collision_key(nfd))



class XmlFixtureTests(XmlFixtureBase):
    def test_recursive_playlist_lookup(self) -> None:
        root = load_dj_playlists(self.xml_path)
        found = find_playlists_by_name(root, "Untitled Intelligent List")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].get("Entries"), "3")
        self.assertEqual(find_playlists_by_name(root, "missing"), [])

    def test_duplicate_playlist_name(self) -> None:
        root = load_dj_playlists(self.xml_path)
        playlists = root.find("PLAYLISTS/NODE")
        assert playlists is not None
        ET.SubElement(playlists, "NODE", {"Name": "Untitled Intelligent List", "Type": "1", "KeyType": "0", "Entries": "0"})
        dup = self.root / "dup.xml"
        ET.ElementTree(root).write(dup, encoding="UTF-8", xml_declaration=True)
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]):
            _, errors = rb.prepare(dup, "Untitled Intelligent List", self.wav_dir, self.output)
        self.assertTrue(any("duplicate playlist name" in e for e in errors))
        joined = "\n".join(errors)
        self.assertIn("Intelligent playlists / Untitled Intelligent List", joined)

    def test_duplicate_playlist_resolved_by_folder(self) -> None:
        root = load_dj_playlists(self.xml_path)
        playlists = root.find("PLAYLISTS/NODE")
        assert playlists is not None
        ET.SubElement(
            playlists,
            "NODE",
            {"Name": "Untitled Intelligent List", "Type": "1", "KeyType": "0", "Entries": "0"},
        )
        dup = self.root / "dup-folder.xml"
        ET.ElementTree(root).write(dup, encoding="UTF-8", xml_declaration=True)
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(
                dup,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
                playlist_folder="Intelligent playlists",
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 3)

    def test_duplicate_playlist_resolved_by_path(self) -> None:
        root = load_dj_playlists(self.xml_path)
        playlists = root.find("PLAYLISTS/NODE")
        assert playlists is not None
        ET.SubElement(
            playlists,
            "NODE",
            {"Name": "Untitled Intelligent List", "Type": "1", "KeyType": "0", "Entries": "0"},
        )
        dup = self.root / "dup-path.xml"
        ET.ElementTree(root).write(dup, encoding="UTF-8", xml_declaration=True)
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(
                dup,
                "Intelligent playlists / Untitled Intelligent List",
                self.wav_dir,
                self.output,
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(plan.playlist_name, "Untitled Intelligent List")
        self.assertEqual(len(plan.unique), 3)

    def test_missing_source_file_skipped_with_warning(self) -> None:
        self.c.unlink()
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(
                self.xml_path, "Untitled Intelligent List", self.wav_dir, self.output
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 2)
        self.assertEqual(len(plan.tracks), 2)
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("missing source file", plan.warnings[0])
        self.assertIn(str(self.c), plan.warnings[0])

    def test_prepare_track_keys_filters_to_subset(self) -> None:
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
                track_keys={"219211420"},
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.tracks), 1)
        self.assertEqual(plan.tracks[0].source_path, self.a)

    def test_missing_collection_key(self) -> None:
        root = load_dj_playlists(self.xml_path)
        node = find_playlists_by_name(root, "Untitled Intelligent List")[0]
        ET.SubElement(node, "TRACK", {"Key": "999"})
        bad = self.root / "missing-key.xml"
        ET.ElementTree(root).write(bad, encoding="UTF-8", xml_declaration=True)
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            _, errors = rb.prepare(bad, "Untitled Intelligent List", self.wav_dir, self.output)
        self.assertTrue(any("missing collection track" in e for e in errors))

    def test_invalid_url(self) -> None:
        root = load_dj_playlists(self.xml_path)
        track = root.find("COLLECTION/TRACK")
        assert track is not None
        track.set("Location", "http://example.com/x.flac")
        bad = self.root / "bad-url.xml"
        ET.ElementTree(root).write(bad, encoding="UTF-8", xml_declaration=True)
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            _, errors = rb.prepare(bad, "Untitled Intelligent List", self.wav_dir, self.output)
        self.assertTrue(any("invalid Rekordbox file URL" in e for e in errors))



if __name__ == "__main__":
    unittest.main()
