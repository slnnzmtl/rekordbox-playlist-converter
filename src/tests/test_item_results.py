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
    format_finish_result_groups,
    format_finish_result_rows,
    format_import_guidance,
    format_playlist_report,
    stats_for_playlist,
    summarize_item_results,
)
from xml_output import PlaylistApplyResult
from convert.paths import source_key
from convert.write import convert_unique
from convert_fixtures import write_pcm_wav
from rekordbox_xml import encode_location


def _result(
    name: str,
    *,
    action: str = "recreate_missing",
    write: str = "transcode",
    reason: str = "",
    playlists: tuple[str, ...] = (),
    ext: str = "flac",
) -> ItemResult:
    return ItemResult(
        source=Path(f"{name}.{ext}"),
        destination=Path(f"{name.upper()}.wav"),
        action=action,
        outcome="succeeded",
        write=write,
        reason=reason,
        playlists=playlists,
    )


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

    def test_report_title_is_partial_when_playlist_not_fully_synced(self) -> None:
        """Given successes plus PlaylistApplyResult(fully_synced=False): When
        titling: Then the report is Partial, not Done."""
        stats = ConvertStats(
            converted=1,
            item_results=[
                ItemResult(
                    source=Path("a.flac"),
                    destination=Path("WAV/A.wav"),
                    action="transcode",
                    outcome="succeeded",
                    write="transcode",
                )
            ],
            playlist_results=[PlaylistApplyResult(fully_synced=False)],
        )
        self.assertEqual(conversion_report_title(stats), "Partial")

    def test_report_title_is_partial_when_selected_track_is_missing(self) -> None:
        """Given successes plus a missing selected track: When titling: Then
        the report is Partial, not Done."""
        stats = ConvertStats(
            converted=1,
            item_results=[
                ItemResult(
                    source=Path("a.flac"),
                    destination=Path("WAV/A.wav"),
                    action="transcode",
                    outcome="succeeded",
                    write="transcode",
                )
            ],
        )
        self.assertEqual(conversion_report_title(stats, missing=1), "Partial")

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

    def test_format_conversion_counts_does_not_mutate_stats(self) -> None:
        """Given item_results and empty counters: When formatting: Then stats stay empty."""
        stats = ConvertStats(
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
        parts = format_conversion_counts(stats)
        self.assertEqual(parts, ["1 converted"])
        self.assertEqual(stats.converted, 0)
        self.assertEqual(stats.copied, 0)
        self.assertEqual(stats.errors, [])

    def test_recreated_counts_are_exclusive_of_converted_and_copied(self) -> None:
        """Given three recreate_missing successes: When formatting counts:
        Then the line is exclusive (recreated wrapping transcode/copy)."""
        results = [
            _result(
                "a",
                reason="dest_missing",
                playlists=("Night [WAV]", "Morning [WAV]"),
            ),
            _result("b", reason="dest_missing", playlists=("Night [WAV]",)),
            _result(
                "c",
                write="copy",
                reason="dest_missing",
                playlists=("Night [WAV]",),
                ext="wav",
            ),
        ]
        stats = ConvertStats(item_results=results)
        self.assertEqual(
            format_conversion_counts(stats),
            ["3 recreated (2 transcoded, 1 copied)"],
        )
        self.assertEqual(
            format_conversion_counts(stats_for_playlist(stats, "Night [WAV]")),
            ["3 recreated (2 transcoded, 1 copied)"],
        )
        self.assertEqual(
            format_conversion_counts(stats_for_playlist(stats, "Morning [WAV]")),
            ["1 recreated (1 transcoded)"],
        )

    def test_not_converted_recreate_counts_as_converted_or_copied(self) -> None:
        """Given first-run recreate_missing (not_converted): When formatting:
        Then counts are converted/copied, not recreated."""
        stats = ConvertStats(
            item_results=[
                _result("a", reason="not_converted"),
                _result("b", write="copy", reason="not_converted", ext="wav"),
            ]
        )
        self.assertEqual(
            format_conversion_counts(stats),
            ["1 converted", "1 copied"],
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

    def test_failed_item_error_includes_source_and_destination(self) -> None:
        """Given a failed ItemResult with an error: When summarizing: Then the
        line is source → dest: error (DDD-145)."""
        summary = summarize_item_results(
            [
                ItemResult(
                    source=Path("/music/source.flac"),
                    destination=Path("/out/WAV/output.wav"),
                    action="transcode",
                    outcome="failed",
                    error="ffmpeg exited 1",
                )
            ]
        )
        self.assertEqual(
            summary.errors,
            ("source.flac → WAV/output.wav: ffmpeg exited 1",),
        )

    def test_format_playlist_report_refreshed_incomplete_and_missing(self) -> None:
        """Given apply results and missing paths: When formatting: Then
        refreshed / not created or refreshed / per-playlist missing lines appear."""
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
            incomplete[0],
            "Night [WAV]: 1 conflict, generated playlist was not created or refreshed",
        )
        self.assertEqual(incomplete[1], "Missing skipped:")
        self.assertEqual(incomplete[2], "/missing/a.flac")

    def test_import_guidance_omits_import_playlist_when_none_synced(self) -> None:
        """Given no fully_synced playlist: When formatting import hints: Then
        Import Playlist is omitted and the missing NODE is explained."""
        text = format_import_guidance(
            Path("/out/rekordbox-import.xml"),
            output_format="wav",
            playlists=[("Night [WAV]", PlaylistApplyResult(fully_synced=False))],
        )
        self.assertIn("Imported Library", text)
        self.assertIn("was not created or refreshed", text)
        self.assertNotIn("Import Playlist", text)
        self.assertNotIn("drag the", text.casefold())

    def test_import_guidance_lists_only_fully_synced_playlists(self) -> None:
        """Given a mixed batch: When formatting import hints: Then only fully
        synced playlists are listed as safe to import."""
        text = format_import_guidance(
            Path("/out/rekordbox-import.xml"),
            output_format="wav",
            playlists=[
                ("Night [WAV]", PlaylistApplyResult(fully_synced=False)),
                ("Morning [WAV]", PlaylistApplyResult(appended=2, fully_synced=True)),
            ],
        )
        self.assertIn("Import Playlist", text)
        self.assertIn("Morning [WAV]", text)
        self.assertIn("safe to import", text.casefold())
        self.assertNotIn("Night [WAV]", text.split("Import Playlist")[-1])

    def test_format_finish_result_rows_statuses_and_missing(self) -> None:
        """Given succeeded/failed/conflict items and missing warnings: When
        formatting finish rows: Then Failed/Conflict Detail reuse CLI
        association strings; success Detail stays dest-only."""
        rows = format_finish_result_rows(
            [
                ItemResult(
                    source=Path("/music/a.flac"),
                    destination=Path("/out/WAV/a.wav"),
                    action="transcode",
                    outcome="succeeded",
                    write="transcode",
                ),
                ItemResult(
                    source=Path("/music/b.flac"),
                    destination=Path("/out/WAV/b.wav"),
                    action="reuse",
                    outcome="succeeded",
                ),
                ItemResult(
                    source=Path("/music/c.flac"),
                    destination=Path("/out/WAV/c.wav"),
                    action="transcode",
                    outcome="failed",
                    error="ffmpeg exited 1",
                ),
                ItemResult(
                    source=Path("/music/d.flac"),
                    destination=Path("/out/WAV/d.wav"),
                    action="transcode",
                    outcome="conflict",
                ),
            ],
            missing=["missing source file: /music/gone.flac"],
        )
        by_track = {row.track: row for row in rows}
        self.assertEqual(by_track["a.flac"].status, "Converted")
        self.assertEqual(by_track["a.flac"].detail, "WAV/a.wav")
        self.assertEqual(by_track["b.flac"].status, "Reused")
        self.assertEqual(by_track["c.flac"].status, "Failed")
        self.assertEqual(
            by_track["c.flac"].detail, "c.flac → WAV/c.wav: ffmpeg exited 1"
        )
        self.assertEqual(by_track["d.flac"].status, "Conflict")
        self.assertEqual(by_track["d.flac"].detail, "d.flac → WAV/d.wav")
        self.assertEqual(by_track["gone.flac"].status, "Missing")
        self.assertEqual(by_track["gone.flac"].detail, "/music/gone.flac")

    def test_format_finish_result_groups_use_status_line_headers(self) -> None:
        """Given two playlists: When grouping finish rows: Then each status
        line is a group header with that playlist's tracks underneath."""
        morning_parts = format_conversion_counts(
            ConvertStats(
                item_results=[
                    ItemResult(
                        source=Path("/music/a.flac"),
                        destination=Path("/out/WAV/a.wav"),
                        action="transcode",
                        outcome="succeeded",
                        write="transcode",
                        playlists=("Morning [WAV]",),
                    ),
                ]
            ),
            missing=1,
        )
        morning_header = format_playlist_report(
            "Morning [WAV]",
            PlaylistApplyResult(appended=1),
            count_parts=morning_parts,
            missing=None,
        )[0]
        groups = format_finish_result_groups(
            [
                ItemResult(
                    source=Path("/music/a.flac"),
                    destination=Path("/out/WAV/a.wav"),
                    action="transcode",
                    outcome="succeeded",
                    write="transcode",
                    playlists=("Night [WAV]", "Morning [WAV]"),
                ),
                ItemResult(
                    source=Path("/music/b.flac"),
                    destination=Path("/out/WAV/b.wav"),
                    action="reuse",
                    outcome="succeeded",
                    playlists=("Night [WAV]",),
                ),
            ],
            playlist_summaries=[
                ("Night [WAV]", "Night [WAV]: 1 converted, 1 reused"),
                ("Morning [WAV]", morning_header),
            ],
            missing_by_playlist={
                "Morning [WAV]": ["missing source file: /music/gone.flac"],
            },
        )
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].header, "Night [WAV]: 1 converted, 1 reused")
        self.assertEqual(
            [row.track for row in groups[0].rows], ["a.flac", "b.flac"]
        )
        self.assertIn("1 missing skipped", groups[1].header)
        self.assertEqual(
            [row.track for row in groups[1].rows], ["a.flac", "gone.flac"]
        )


if __name__ == "__main__":
    unittest.main()
