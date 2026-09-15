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
    conversion_exit_code,
    conversion_report_title,
    format_conversion_counts,
    format_playlist_report,
    stats_for_playlist,
)
from xml_output import PlaylistApplyResult
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
            ConvertStats(
                item_results=[
                    ItemResult(
                        source=Path("a.flac"),
                        destination=Path("A.wav"),
                        action="transcode",
                        outcome="state_changed",
                    ),
                    ItemResult(
                        source=Path("b.flac"),
                        destination=Path("B.wav"),
                        action="transcode",
                        outcome="state_changed",
                    ),
                ]
            )
        )
        self.assertIn("2 state-changed", parts)

    def test_recreated_counts_are_exclusive_of_converted_and_copied(self) -> None:
        """Given three recreate_missing successes: When formatting counts:
        Then the line is exclusive (recreated wrapping transcode/copy)."""
        results = [
            ItemResult(
                source=Path("a.flac"),
                destination=Path("A.wav"),
                action="recreate_missing",
                outcome="succeeded",
                write="transcode",
                playlists=("Night [WAV]", "Morning [WAV]"),
            ),
            ItemResult(
                source=Path("b.flac"),
                destination=Path("B.wav"),
                action="recreate_missing",
                outcome="succeeded",
                write="transcode",
                playlists=("Night [WAV]",),
            ),
            ItemResult(
                source=Path("c.wav"),
                destination=Path("C.wav"),
                action="recreate_missing",
                outcome="succeeded",
                write="copy",
                playlists=("Night [WAV]",),
            ),
        ]
        stats = ConvertStats(item_results=results)
        parts = format_conversion_counts(stats)
        self.assertEqual(parts, ["3 recreated (2 transcoded, 1 copied)"])
        night = stats_for_playlist(stats, "Night [WAV]")
        morning = stats_for_playlist(stats, "Morning [WAV]")
        self.assertEqual(
            format_conversion_counts(night),
            ["3 recreated (2 transcoded, 1 copied)"],
        )
        self.assertEqual(
            format_conversion_counts(morning),
            ["1 recreated (1 transcoded)"],
        )

    def test_exit_code_nonzero_for_conflicts_and_state_changed(self) -> None:
        ok = ConvertStats(
            item_results=[
                ItemResult(
                    source=Path("a.flac"),
                    destination=Path("A.wav"),
                    action="transcode",
                    outcome="succeeded",
                    write="transcode",
                )
            ]
        )
        self.assertEqual(conversion_exit_code(ok), 0)
        conflict = ConvertStats(
            item_results=[
                ItemResult(
                    source=Path("a.flac"),
                    destination=Path("A.wav"),
                    action="transcode",
                    outcome="conflict",
                )
            ]
        )
        self.assertEqual(conversion_exit_code(conflict), 1)
        changed = ConvertStats(
            item_results=[
                ItemResult(
                    source=Path("a.flac"),
                    destination=Path("A.wav"),
                    action="transcode",
                    outcome="state_changed",
                )
            ]
        )
        self.assertEqual(conversion_exit_code(changed), 1)
        failed = ConvertStats(
            item_results=[
                ItemResult(
                    source=Path("a.flac"),
                    destination=Path("A.wav"),
                    action="transcode",
                    outcome="failed",
                    error="boom",
                )
            ]
        )
        self.assertEqual(conversion_exit_code(failed), 1)

    def test_format_playlist_report_refreshed_incomplete_and_missing(self) -> None:
        """Given apply results and missing paths: When formatting: Then
        refreshed / not fully refreshed / per-playlist missing lines appear."""
        refreshed = format_playlist_report(
            "Night [WAV]",
            PlaylistApplyResult(removed=1, reordered=False, fully_synced=True),
            count_parts=["2 reused"],
            missing=[],
        )
        self.assertEqual(refreshed[0], "Night [WAV]: 2 reused, playlist refreshed")

        appended_only = format_playlist_report(
            "Night [WAV]",
            PlaylistApplyResult(appended=2, fully_synced=True),
            count_parts=["2 converted"],
            missing=[],
        )
        self.assertEqual(
            appended_only[0], "Night [WAV]: 2 converted, +2 playlist entries"
        )

        incomplete = format_playlist_report(
            "Night [WAV]",
            PlaylistApplyResult(fully_synced=False),
            count_parts=["1 conflict"],
            missing=["/missing/a.flac"],
        )
        self.assertEqual(
            incomplete[0], "Night [WAV]: 1 conflict, playlist not fully refreshed"
        )
        self.assertEqual(incomplete[1], "Missing skipped:")
        self.assertEqual(incomplete[2], "/missing/a.flac")


if __name__ == "__main__":
    unittest.main()
