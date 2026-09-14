#!/usr/bin/env python3
"""ItemResult collection and derived conversion report counts."""

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

import convert.plan
import converter_manifest
from convert.freshness import bind_complete_assignment
from convert.models import (
    ConvertStats,
    ItemResult,
    Plan,
    PlannedTrack,
    conversion_report_title,
    format_conversion_counts,
)
from convert.paths import source_key
from convert.write import convert_unique
from convert_fixtures import write_pcm_wav
from rekordbox_xml import encode_location


class ItemResultDeriveTests(unittest.TestCase):
    def test_pcm_rebuild_is_not_also_copied_in_counts(self) -> None:
        """Given a container rewrite: When convert_unique succeeds: Then
        pcm_rebuilt increments without also counting copied."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.wav"
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(src, frames=8)
            write_pcm_wav(dest, frames=8)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=encode_location(dest),
                dest_name=dest.name,
                codec=None,
                passthrough=True,
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
            with patch.object(convert.plan, "run_ffmpeg") as ffmpeg:
                stats = convert_unique(plan, force=False)
            ffmpeg.assert_not_called()
            self.assertEqual(stats.pcm_rebuilt, 1)
            self.assertEqual(stats.copied, 0)
            self.assertEqual(stats.converted, 0)
            self.assertEqual(len(stats.item_results), 1)
            result = stats.item_results[0]
            self.assertEqual(result.outcome, "succeeded")
            self.assertEqual(result.action, "rewrite_container")
            self.assertEqual(result.source, src)
            self.assertEqual(result.destination, dest)

    def test_report_title_all_failure_is_failed_not_done(self) -> None:
        stats = ConvertStats(
            errors=["boom"],
            item_results=[
                ItemResult(
                    source=Path("a.flac"),
                    destination=Path("A.wav"),
                    action="transcode",
                    outcome="failed",
                    error="boom",
                )
            ],
        )
        self.assertEqual(conversion_report_title(stats), "Failed")
        self.assertEqual(
            conversion_report_title(ConvertStats(converted=1)),
            "Done",
        )
        self.assertEqual(
            conversion_report_title(
                ConvertStats(converted=1, conflicts=["A.wav"])
            ),
            "Partial",
        )
        self.assertEqual(
            conversion_report_title(ConvertStats(), cancelled=True),
            "Cancelled",
        )
        self.assertEqual(conversion_report_title(ConvertStats()), "No conversions")

    def test_format_counts_includes_state_changed(self) -> None:
        parts = format_conversion_counts(
            ConvertStats(state_changed=["A.wav", "B.wav"])
        )
        self.assertIn("2 state-changed", parts)


if __name__ == "__main__":
    unittest.main()
