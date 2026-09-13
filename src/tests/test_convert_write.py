#!/usr/bin/env python3
"""Write-port tests: execute_prepared owns save + convert + apply + write."""

from __future__ import annotations

import sys
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
import convert_plan
import ffmpeg_tools
import rb_playlist_to_wav as rb
from convert.write import execute_prepared
from convert_fixtures import XmlFixtureTests as XmlFixtureBase


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
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            prepared, errors = rb.prepare_batch(
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
        names = [name for _folder, name, _node in rb.iter_playlists(out)]
        self.assertEqual(names, ["Untitled Intelligent List [WAV]"])
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)


if __name__ == "__main__":
    unittest.main()
