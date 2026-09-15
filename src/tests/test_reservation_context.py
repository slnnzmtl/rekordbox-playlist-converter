#!/usr/bin/env python3
"""Acceptance tests for batch-scoped ReservationContext (DDD-154 Slice B)."""

from __future__ import annotations

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

import converter_manifest as cm
import ffmpeg_tools
from convert.paths import collision_key
from convert.prepare import prepare, prepare_batch
from convert_fixtures import XmlFixtureTests as XmlFixtureBase, write_flac
from rekordbox_xml import encode_location, load_dj_playlists


class ReservationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wav_dir = Path(self.tmp.name) / "lib"
        self.wav_dir.mkdir()
        self.wav_fmt = self.wav_dir / "WAV"
        self.wav_fmt.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_same_file_source_in_inventory_is_unoccupied(self) -> None:
        """Given inventory listing the source at the preferred dest: When
        occupancy is checked for that source: Then the dest is not occupied."""
        source = self.wav_fmt / "Track.wav"
        source.write_bytes(b"RIFF")
        ctx = cm.ReservationContext(self.wav_dir)
        ctx.refresh_inventory()
        occupied = cm.relative_dest_occupied(
            cm.empty_manifest(),
            "WAV/Track.wav",
            wav_dir=self.wav_dir,
            source_path=source,
            reservation=ctx,
        )
        self.assertFalse(occupied)

    def test_case_equivalent_inventory_paths_occupy_preferred_dest(self) -> None:
        """Given inventory with multiple case-equivalent paths that are not the
        source: When occupancy is checked for preferred Track.wav: Then occupied."""
        upper = self.wav_fmt / "Track.wav"
        lower = self.wav_fmt / "track.wav"
        source = self.wav_dir / "elsewhere" / "original.flac"
        source.parent.mkdir()
        source.write_bytes(b"fLaC")
        key = collision_key("Track.wav")
        ctx = cm.ReservationContext(
            self.wav_dir,
            inventory={key: (upper, lower)},
        )
        occupied = cm.relative_dest_occupied(
            cm.empty_manifest(),
            "WAV/Track.wav",
            wav_dir=self.wav_dir,
            source_path=source,
            reservation=ctx,
        )
        self.assertTrue(occupied)

    def test_refresh_inventory_sees_newly_created_destinations(self) -> None:
        """Given a prepared inventory: When a new file appears on disk and
        refresh_inventory runs: Then occupancy sees the new dest."""
        ctx = cm.ReservationContext(self.wav_dir)
        ctx.refresh_inventory()
        preferred = "WAV/Late Arrival.wav"
        before = cm.relative_dest_occupied(
            cm.empty_manifest(),
            preferred,
            wav_dir=self.wav_dir,
            reservation=ctx,
        )
        self.assertFalse(before)
        (self.wav_fmt / "Late Arrival.wav").write_bytes(b"NEW")
        still_stale = cm.relative_dest_occupied(
            cm.empty_manifest(),
            preferred,
            wav_dir=self.wav_dir,
            reservation=ctx,
        )
        self.assertFalse(
            still_stale,
            "inventory must not see disk changes until refresh",
        )
        ctx.refresh_inventory()
        after = cm.relative_dest_occupied(
            cm.empty_manifest(),
            preferred,
            wav_dir=self.wav_dir,
            reservation=ctx,
        )
        self.assertTrue(after)

    def test_sticky_assignment_returns_without_free_name_probing(self) -> None:
        """Given a sticky dest already in the manifest: When reserve_relative_dest
        runs with a ReservationContext: Then the sticky path is returned and
        relative_dest_occupied is not consulted."""
        manifest = cm.empty_manifest()
        sk = "/music/sticky.flac"
        sticky = "WAV/Artist - Sticky.wav"
        manifest.set_dest(sk, "wav", sticky)
        ctx = cm.ReservationContext(self.wav_dir)
        with patch.object(
            cm, "relative_dest_occupied", wraps=cm.relative_dest_occupied
        ) as occupied:
            dest = cm.reserve_relative_dest(
                manifest,
                source_key=sk,
                output_format="wav",
                preferred="WAV/Artist - Other.wav",
                wav_dir=self.wav_dir,
                reservation=ctx,
            )
        self.assertEqual(dest, sticky)
        occupied.assert_not_called()

    def test_suffix_cursor_advances_past_already_reserved_numbers(self) -> None:
        """Given three identical preferred names: When reserved via one
        ReservationContext: Then dests are Name.wav, Name (2).wav, Name (3).wav
        and the third reserve does not re-probe Name (2).wav as free."""
        manifest = cm.empty_manifest()
        ctx = cm.ReservationContext(self.wav_dir)
        preferred = "WAV/Same - Song.wav"
        probe_targets: list[str] = []
        real_occupied = cm.relative_dest_occupied

        def tracking_occupied(*args: object, **kwargs: object) -> bool:
            relative = args[1] if len(args) > 1 else kwargs.get("relative_dest")
            if isinstance(relative, str):
                probe_targets.append(relative)
            return real_occupied(*args, **kwargs)  # type: ignore[arg-type]

        with patch.object(cm, "relative_dest_occupied", side_effect=tracking_occupied):
            first = cm.reserve_relative_dest(
                manifest,
                source_key="/a",
                output_format="wav",
                preferred=preferred,
                wav_dir=self.wav_dir,
                reservation=ctx,
            )
            second = cm.reserve_relative_dest(
                manifest,
                source_key="/b",
                output_format="wav",
                preferred=preferred,
                wav_dir=self.wav_dir,
                reservation=ctx,
            )
            probes_before_third = list(probe_targets)
            third = cm.reserve_relative_dest(
                manifest,
                source_key="/c",
                output_format="wav",
                preferred=preferred,
                wav_dir=self.wav_dir,
                reservation=ctx,
            )
            probes_during_third = probe_targets[len(probes_before_third) :]

        self.assertEqual(first, "WAV/Same - Song.wav")
        self.assertEqual(second, "WAV/Same - Song (2).wav")
        self.assertEqual(third, "WAV/Same - Song (3).wav")
        self.assertNotIn(
            "WAV/Same - Song (2).wav",
            probes_during_third,
            "suffix cursor must start at (3) after Song and Song (2) are taken",
        )

    def test_reserve_with_reservation_none_still_names_normally(self) -> None:
        """Given reservation=None: When reserve_relative_dest assigns: Then
        preferred is kept when free (legacy prepare path without a shared ctx)."""
        manifest = cm.empty_manifest()
        dest = cm.reserve_relative_dest(
            manifest,
            source_key="/music/a.flac",
            output_format="wav",
            preferred="WAV/Artist - Track.wav",
            wav_dir=self.wav_dir,
            reservation=None,
        )
        self.assertEqual(dest, "WAV/Artist - Track.wav")
        self.assertEqual(
            manifest.get_dest("/music/a.flac", "wav"),
            "WAV/Artist - Track.wav",
        )


class ReservationContextBatchTests(XmlFixtureBase):
    def test_prepare_batch_scans_format_dir_once_for_two_playlists(self) -> None:
        """Given two playlists in one prepare_batch: When destinations are
        reserved: Then Path.iterdir on the WAV format dir runs once for the
        batch, not once per playlist."""
        root = load_dj_playlists(self.xml_path)
        playlists_root = root.find("PLAYLISTS/NODE")
        assert playlists_root is not None
        morning = ET.SubElement(
            playlists_root,
            "NODE",
            {"Name": "Morning", "Type": "1", "KeyType": "0", "Entries": "1"},
        )
        ET.SubElement(morning, "TRACK", {"Key": "58834508"})
        playlists_root.set("Count", "2")
        ET.ElementTree(root).write(
            self.xml_path, encoding="UTF-8", xml_declaration=True
        )

        self.wav_fmt = self.wav_dir / "WAV"
        self.wav_fmt.mkdir(parents=True)
        (self.wav_fmt / "filler.wav").write_bytes(b"FILL")
        wav_fmt_resolved = self.wav_fmt.resolve()

        real_iterdir = Path.iterdir
        iterdir_calls = 0

        def counting_iterdir(path_self: Path):
            nonlocal iterdir_calls
            try:
                if path_self.resolve() == wav_fmt_resolved:
                    iterdir_calls += 1
            except OSError:
                pass
            return real_iterdir(path_self)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(Path, "iterdir", counting_iterdir):
            prepared, errors = prepare_batch(
                self.xml_path,
                [
                    (None, "Untitled Intelligent List"),
                    (None, "Morning"),
                ],
                self.wav_dir,
                self.output,
            )
        self.assertEqual(errors, [])
        assert prepared is not None
        self.assertEqual(
            iterdir_calls,
            1,
            f"expected one WAV inventory scan for the batch, got {iterdir_calls}",
        )

    def test_prepare_without_explicit_context_still_assigns_dests(self) -> None:
        """Given prepare() with no ReservationContext argument: When planning
        three identical names: Then dests match Name, Name (2), Name (3)."""
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
           Location="{encode_location(srcs[0])}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="Song" Artist="Same" Album="Hits"
           Location="{encode_location(srcs[1])}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="Song" Artist="Same" Album="Hits"
           Location="{encode_location(srcs[2])}" Kind="FLAC File"/>
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
        path = self.root / "triple-ctx.xml"
        path.write_text(xml, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = prepare(path, "Triple", self.wav_dir, self.output)
        self.assertEqual(errors, [])
        assert plan is not None
        dests = {
            u.source_path: u.dest_path.relative_to(plan.library_dir).as_posix()
            for u in plan.unique
        }
        self.assertEqual(dests[srcs[0]], "WAV/Same - Song.wav")
        self.assertEqual(dests[srcs[1]], "WAV/Same - Song (2).wav")
        self.assertEqual(dests[srcs[2]], "WAV/Same - Song (3).wav")


if __name__ == "__main__":
    unittest.main()
