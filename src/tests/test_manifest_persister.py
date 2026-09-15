#!/usr/bin/env python3
"""Acceptance tests for time-based ManifestPersister (DDD-154 Slice C)."""

from __future__ import annotations

import concurrent.futures
import json
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_SRC, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import converter_manifest
import convert.plan
import convert.write as write_mod
import ffmpeg_tools
from convert.freshness import assignment_state
from convert.paths import source_key
from convert.prepare import prepare_batch
from convert.write import execute_prepared
from convert_fixtures import XmlFixtureTests as XmlFixtureBase


class _FakeClock:
    """Injectable monotonic clock; advance explicitly (never sleep)."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._t

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._t += float(seconds)


def _marker_tracks(marker: str) -> dict:
    return {
        f"/src/{marker}": {
            "wav": {
                "dest": f"WAV/{marker}.wav",
                "state": "complete",
                "source": {"size": 1, "mtime_ns": 1},
                "metadata": {"signature": marker},
                "output": {"size": 1, "mtime_ns": 1},
                "recipe": {
                    "format": "wav",
                    "bit_depth": 16,
                    "sample_rate": 44100,
                    "channels": 2,
                    "revision": 1,
                },
            }
        }
    }


def _disk_marker(library_dir: Path) -> str | None:
    """Read marker from disk without full manifest validation (synthetic sigs)."""
    path = library_dir / converter_manifest.MANIFEST_NAME
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    for formats in data.get("tracks", {}).values():
        rec = formats.get("wav") or {}
        meta = rec.get("metadata") or {}
        sig = meta.get("signature")
        if isinstance(sig, str):
            return sig
    return None


class ManifestPersisterUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.library_dir = Path(self.tmp.name) / "lib"
        self.library_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_older_periodic_cannot_replace_newer_durable_sequence(self) -> None:
        """Given seq=1 periodic blocked before persist: When seq=2 urgent
        becomes durable: Then when seq=1 resumes it must not overwrite disk."""
        started = threading.Event()
        release = threading.Event()
        before_count = {"n": 0}

        def before_persist() -> None:
            before_count["n"] += 1
            if before_count["n"] == 1:
                started.set()
                self.assertTrue(release.wait(timeout=5), "release gate timed out")

        real_save = converter_manifest.save_manifest_tracks

        def save_tracks(tracks, library_dir):
            real_save(tracks, library_dir)

        clock = _FakeClock(0.0)
        persister = write_mod.ManifestPersister(
            self.library_dir,
            checkpoint_interval_s=10.0,
            clock=clock,
            save_tracks=save_tracks,
            before_persist=before_persist,
        )
        older = _marker_tracks("older")
        newer = _marker_tracks("newer")

        def run_older() -> None:
            persister.request_periodic(older)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(run_older)
            self.assertTrue(started.wait(timeout=5))
            persister.save_urgent(newer)
            self.assertEqual(_disk_marker(self.library_dir), "newer")
            mtime_after_newer = (
                self.library_dir / converter_manifest.MANIFEST_NAME
            ).stat().st_mtime_ns
            release.set()
            fut.result(timeout=5)
            persister.join()

        self.assertEqual(_disk_marker(self.library_dir), "newer")
        self.assertEqual(
            (self.library_dir / converter_manifest.MANIFEST_NAME).stat().st_mtime_ns,
            mtime_after_newer,
        )

    def test_urgent_save_waits_for_earlier_periodic_and_remains_newest(self) -> None:
        """Given a slow in-flight periodic: When urgent save runs: Then it
        waits, then disk ends with the urgent snapshot."""
        entered_periodic = threading.Event()
        release_periodic = threading.Event()
        order: list[str] = []

        real_save = converter_manifest.save_manifest_tracks

        def save_tracks(tracks, library_dir):
            marker = None
            for formats in tracks.values():
                rec = formats.get("wav") or {}
                marker = (rec.get("metadata") or {}).get("signature")
            if marker == "periodic":
                order.append("periodic_enter")
                entered_periodic.set()
                self.assertTrue(
                    release_periodic.wait(timeout=5), "periodic release timed out"
                )
                real_save(tracks, library_dir)
                order.append("periodic_done")
            else:
                real_save(tracks, library_dir)
                order.append(f"saved:{marker}")

        persister = write_mod.ManifestPersister(
            self.library_dir,
            checkpoint_interval_s=10.0,
            clock=_FakeClock(0.0),
            save_tracks=save_tracks,
        )
        periodic = _marker_tracks("periodic")
        urgent = _marker_tracks("urgent")

        def run_periodic() -> None:
            persister.request_periodic(periodic)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(run_periodic)
            self.assertTrue(entered_periodic.wait(timeout=5))
            urgent_done = threading.Event()

            def run_urgent() -> None:
                persister.save_urgent(urgent)
                urgent_done.set()

            urgent_fut = pool.submit(run_urgent)
            self.assertFalse(
                urgent_done.wait(timeout=0.05),
                "urgent must wait while periodic holds the save lock",
            )
            release_periodic.set()
            fut.result(timeout=5)
            urgent_fut.result(timeout=5)
            persister.join()

        self.assertEqual(_disk_marker(self.library_dir), "urgent")
        self.assertIn("periodic_done", order)
        self.assertEqual(order[-1], "saved:urgent")

    def test_periodic_failure_then_successful_final_is_safe(self) -> None:
        """Given a periodic save that raises once: When final save succeeds:
        Then disk has the final snapshot."""
        real_save = converter_manifest.save_manifest_tracks

        def save_tracks(tracks, library_dir):
            marker = None
            for formats in tracks.values():
                rec = formats.get("wav") or {}
                marker = (rec.get("metadata") or {}).get("signature")
            if marker == "periodic":
                raise OSError("periodic save failed")
            real_save(tracks, library_dir)

        persister = write_mod.ManifestPersister(
            self.library_dir,
            checkpoint_interval_s=10.0,
            clock=_FakeClock(0.0),
            save_tracks=save_tracks,
        )
        try:
            persister.request_periodic(_marker_tracks("periodic"))
            persister.join()
        except OSError:
            pass
        persister.save_final(_marker_tracks("final"))
        self.assertEqual(_disk_marker(self.library_dir), "final")


class ManifestPersisterExecutePreparedTests(XmlFixtureBase):
    def test_final_save_failure_prevents_import_xml_commit(self) -> None:
        """Given patched save_manifest_tracks that fails on final: When
        execute_prepared runs: Then it raises and Import XML is not written."""
        real_save = converter_manifest.save_manifest_tracks
        saves = {"n": 0}

        def tracking_save(tracks, wav_dir):
            saves["n"] += 1
            # prebatch is first; with interval disabled the next save is final
            if saves["n"] >= 2:
                raise OSError("final manifest save failed")
            real_save(tracks, wav_dir)

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            converter_manifest, "save_manifest_tracks", side_effect=tracking_save
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            with self.assertRaises(OSError):
                execute_prepared(
                    prepared,
                    force=False,
                    progress=False,
                    checkpoint_interval_s=0,
                )

        self.assertFalse(self.output.is_file())

    def test_no_mid_batch_periodic_without_clock_advance(self) -> None:
        """Given checkpoint_interval_s=10 and a frozen clock: When many tracks
        complete without advancing time: Then only prebatch and final saves
        occur (no mid-batch periodics)."""
        saves: list[dict] = []
        real_save = converter_manifest.save_manifest_tracks
        clock = _FakeClock(0.0)

        def tracking_save(tracks, wav_dir):
            saves.append(deepcopy(tracks))
            real_save(tracks, wav_dir)

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            converter_manifest, "save_manifest_tracks", side_effect=tracking_save
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            execute_prepared(
                prepared,
                force=False,
                progress=False,
                workers=1,
                checkpoint_interval_s=10.0,
                clock=clock,
            )

        self.assertGreaterEqual(len(saves), 2)
        completes = [
            sum(
                1
                for formats in snapshot.values()
                if assignment_state(formats["wav"]) == "complete"
            )
            for snapshot in saves
        ]
        self.assertEqual(completes[0], 0)
        self.assertEqual(completes[-1], 3)
        self.assertFalse(
            any(0 < n < 3 for n in completes[1:-1]),
            "without clock advance, mid-batch periodics must not appear",
        )

    def test_clock_advance_triggers_mid_batch_periodic(self) -> None:
        """Given checkpoint_interval_s=10: When the fake clock advances past
        the interval after the first completion: Then a mid-batch periodic
        snapshot appears between prebatch and final."""
        saves: list[dict] = []
        real_save = converter_manifest.save_manifest_tracks
        clock = _FakeClock(0.0)
        advances = {"n": 0}

        def tracking_save(tracks, wav_dir):
            saves.append(deepcopy(tracks))
            real_save(tracks, wav_dir)

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")
            advances["n"] += 1
            if advances["n"] == 1:
                clock.advance(10.0)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            converter_manifest, "save_manifest_tracks", side_effect=tracking_save
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            execute_prepared(
                prepared,
                force=False,
                progress=False,
                workers=1,
                checkpoint_interval_s=10.0,
                clock=clock,
            )

        completes = [
            sum(
                1
                for formats in snapshot.values()
                if assignment_state(formats["wav"]) == "complete"
            )
            for snapshot in saves
        ]
        self.assertEqual(completes[0], 0)
        self.assertEqual(completes[-1], 3)
        self.assertTrue(
            any(0 < n < 3 for n in completes[1:-1]),
            "advancing the clock past the interval must produce a mid-batch periodic",
        )

    def test_interrupt_before_periodic_leaves_completed_outputs_incomplete(
        self,
    ) -> None:
        """Given no periodic (interval<=0): When the first track finishes then
        conversion is interrupted before final save: Then disk keeps prebatch
        incomplete (no false complete for the finished output)."""
        cancel = threading.Event()
        first_done = threading.Event()
        n = {"i": 0}
        lock = threading.Lock()

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            with lock:
                n["i"] += 1
                idx = n["i"]
            if idx == 1:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"RIFF")
                first_done.set()
                cancel.set()
                return
            if cancel.wait(timeout=5):
                from cli_error import CancelledError

                raise CancelledError(f"conversion cancelled for {source}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        real_convert = write_mod.convert_unique

        def convert_then_crash(*args, **kwargs):
            stats = real_convert(*args, **kwargs)
            raise RuntimeError("simulated interrupt before final manifest save")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            write_mod, "convert_unique", side_effect=convert_then_crash
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            with self.assertRaises(RuntimeError):
                execute_prepared(
                    prepared,
                    force=False,
                    progress=False,
                    workers=1,
                    cancel_event=cancel,
                    checkpoint_interval_s=0,
                )

        self.assertTrue(first_done.is_set())
        loaded = converter_manifest.load_manifest(self.wav_dir)
        for item in prepared.items:
            rec = loaded.tracks[source_key(item.source_path)]["wav"]
            self.assertEqual(
                assignment_state(rec),
                "incomplete",
                f"{item.dest_name} must stay incomplete without a durable checkpoint",
            )
        self.assertTrue(prepared.items[0].dest_path.is_file())
        self.assertFalse(self.output.is_file())


if __name__ == "__main__":
    unittest.main()
