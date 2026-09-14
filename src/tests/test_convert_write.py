#!/usr/bin/env python3
"""Write-port tests: execute_prepared owns save + convert + apply + write."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_SRC, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import converter_manifest
import ffmpeg_tools
import cdj_wav
import convert.plan
from convert.freshness import bind_complete_assignment
from convert.models import Plan, PlannedTrack
from convert.write import convert_unique, execute_prepared
from convert_fixtures import XmlFixtureTests as XmlFixtureBase, write_pcm_wav
from convert.prepare import prepare_batch
from rekordbox_xml import encode_location, iter_playlists


class ExecutePreparedTests(XmlFixtureBase):
    def test_execute_prepared_saves_converts_applies_and_writes_import_xml(
        self,
    ) -> None:
        """Given a PreparedConversion: When execute_prepared runs: Then the
        manifest is saved, unique tracks convert once, each plan gets apply_xml,
        and import XML is written."""
        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None

            stats = execute_prepared(prepared, force=False, progress=False)

        self.assertEqual(stats.converted, 3)
        self.assertEqual(len(encoded), 3)
        self.assertTrue((self.wav_dir / converter_manifest.MANIFEST_NAME).is_file())
        for item in prepared.items:
            self.assertTrue(item.dest_path.is_file())
        self.assertTrue(self.output.is_file())
        out = ET.parse(self.output).getroot()
        names = [name for _folder, name, _node in iter_playlists(out)]
        self.assertEqual(names, ["Untitled Intelligent List [WAV]"])
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)

    def test_convert_unique_reclassifies_mtime_change_as_conflict(self) -> None:
        """Given preview would reuse: When dest mtime changes before write:
        Then convert_unique reports a conflict and does not overwrite dest."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            prior = dest.read_bytes()
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
            st = dest.stat()
            os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            encoded: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                encoded.append(dest_path)
                dest_path.write_bytes(b"OVERWRITTEN")

            with patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = convert_unique(plan, force=False)
            self.assertEqual(stats.conflicts, [dest.name])
            self.assertEqual(stats.converted, 0)
            self.assertEqual(encoded, [])
            self.assertEqual(dest.read_bytes(), prior)
            self.assertEqual(stats.succeeded, set())


if __name__ == "__main__":
    unittest.main()
