#!/usr/bin/env python3
"""Deterministic call-count coverage for large-library Manifest V2 paths."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import converter_manifest as cm
import convert.write as write_mod


class ManifestPerfCallCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wav_dir = Path(self.tmp.name) / "lib"
        self.wav_dir.mkdir()
        (self.wav_dir / "WAV").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_hundreds_of_unique_reserves_do_not_rescan_format_dir(self) -> None:
        """Given 200 unique preferred names and one ReservationContext: When
        reserved: Then WAV iterdir runs once (refresh), not once per track."""
        ctx = cm.ReservationContext.scanned(self.wav_dir)
        wav_fmt = (self.wav_dir / "WAV").resolve()
        real_iterdir = Path.iterdir
        scans = {"n": 0}

        def counting_iterdir(path_self: Path):
            try:
                if path_self.resolve() == wav_fmt:
                    scans["n"] += 1
            except OSError:
                pass
            return real_iterdir(path_self)

        manifest = cm.empty_manifest()
        with patch.object(Path, "iterdir", counting_iterdir):
            for i in range(200):
                cm.reserve_relative_dest(
                    manifest,
                    source_key=f"/src/{i}",
                    output_format="wav",
                    preferred=f"WAV/Track {i}.wav",
                    wav_dir=self.wav_dir,
                    reservation=ctx,
                )
        self.assertEqual(
            scans["n"],
            0,
            "reserves must use the batch inventory, not per-track iterdir",
        )
        self.assertEqual(len(manifest.tracks), 200)

    def test_collision_heavy_suffix_probes_are_linear(self) -> None:
        """Given 100 identical preferred names: When reserved via one context:
        Then occupancy probes are O(N), not O(N²)."""
        n = 100
        manifest = cm.empty_manifest()
        ctx = cm.ReservationContext.scanned(self.wav_dir)
        preferred = "WAV/Same - Song.wav"
        probes = {"n": 0}
        real_occupied = cm.relative_dest_occupied

        def counting_occupied(*args, **kwargs):
            probes["n"] += 1
            return real_occupied(*args, **kwargs)

        with patch.object(cm, "relative_dest_occupied", side_effect=counting_occupied):
            for i in range(n):
                cm.reserve_relative_dest(
                    manifest,
                    source_key=f"/src/{i}",
                    output_format="wav",
                    preferred=preferred,
                    wav_dir=self.wav_dir,
                    reservation=ctx,
                )
        # Preferred + each next free: ~2N probes with a suffix cursor (not ~N²/2).
        self.assertLessEqual(probes["n"], 3 * n)
        self.assertEqual(
            manifest.get_dest(f"/src/{n - 1}", "wav"),
            f"WAV/Same - Song ({n}).wav" if n > 1 else preferred,
        )

    def test_checkpoint_save_count_follows_fake_elapsed_time(self) -> None:
        """Given many completions: When the fake clock does not advance: Then
        only urgent/final-style sync saves happen via the persister API, not
        one save per completion."""
        library = self.wav_dir
        clock = {"t": 0.0}
        saves = {"n": 0}

        def save_tracks(tracks, wav_dir):
            saves["n"] += 1

        persister = write_mod.ManifestPersister(
            library,
            checkpoint_interval_s=10.0,
            clock=lambda: clock["t"],
            save_tracks=save_tracks,
        )
        tracks = {"/s": {"wav": {"dest": "WAV/A.wav", "state": "complete"}}}
        for _ in range(50):
            if persister.should_schedule_periodic():
                persister.request_periodic(tracks)
        persister.join()
        self.assertEqual(saves["n"], 0)
        clock["t"] = 10.0
        self.assertTrue(persister.should_schedule_periodic())
        persister.request_periodic(tracks)
        persister.join()
        self.assertEqual(saves["n"], 1)


if __name__ == "__main__":
    unittest.main()
