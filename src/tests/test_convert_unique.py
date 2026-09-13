#!/usr/bin/env python3
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

import rb_playlist_to_wav as rb
import convert_plan
from convert import encode
import ffmpeg_tools
import xml_output
from convert_fixtures import (
    FIXTURE,
    HangProc as _HangProc,
    XmlFixtureTests as XmlFixtureBase,
    flac_probe,
    wav_probe,
    write_flac,
)


class XmlFixtureTests(XmlFixtureBase):
    def test_convert_unique_stops_remaining_tracks_when_cancel_event_set(self) -> None:
        """Cancel mid-run: keep completed dests, drop the rest, return stats (no raise)."""
        import concurrent.futures
        import threading

        cancel = threading.Event()
        first_done = threading.Event()
        n = {"i": 0}
        lock = threading.Lock()

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            with lock:
                n["i"] += 1
                idx = n["i"]
            if idx == 1:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"RIFF")
                first_done.set()
                return
            if cancel.wait(timeout=5):
                raise rb.CancelledError(f"conversion cancelled for {source}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ), patch.object(convert_plan, "CONVERT_WORKERS", 1):
            plan, errors = rb.prepare(
                self.xml_path, "Untitled Intelligent List", self.wav_dir, self.output
            )
            self.assertEqual(errors, [])
            assert plan is not None

            def run() -> convert_plan.ConvertStats:
                return rb.convert_unique(
                    plan, force=False, progress=False, cancel_event=cancel
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(run)
                self.assertTrue(first_done.wait(timeout=5))
                cancel.set()
                stats = fut.result(timeout=10)

        self.assertEqual(stats.converted, 1)
        self.assertTrue(plan.unique[0].dest_path.exists())
        self.assertFalse(plan.unique[-1].dest_path.exists())

    def test_convert_unique_preserves_errors_when_cancelled_after_failure(
        self,
    ) -> None:
        """Given one encode failure then cancel: When convert_unique returns:
        Then stats.errors still lists the boom (not dropped by early cancel return)."""
        import concurrent.futures
        import threading

        cancel = threading.Event()
        first_failed = threading.Event()
        n = {"i": 0}
        lock = threading.Lock()
        boom = "boom for first track"

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            with lock:
                n["i"] += 1
                idx = n["i"]
            if idx == 1:
                first_failed.set()
                raise rb.CliError(boom)
            if cancel.wait(timeout=5):
                raise rb.CancelledError(f"conversion cancelled for {source}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ), patch.object(convert_plan, "CONVERT_WORKERS", 1):
            plan, errors = rb.prepare(
                self.xml_path, "Untitled Intelligent List", self.wav_dir, self.output
            )
            self.assertEqual(errors, [])
            assert plan is not None

            def run() -> convert_plan.ConvertStats:
                return rb.convert_unique(
                    plan, force=False, progress=False, cancel_event=cancel
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(run)
                self.assertTrue(first_failed.wait(timeout=5))
                cancel.set()
                stats = fut.result(timeout=10)

        self.assertTrue(stats.errors)
        joined = "\n".join(stats.errors)
        self.assertIn("boom", joined)

    def test_convert_unique_progress_is_completed_count(self) -> None:
        """Progress current is completed items (1..total), not loop index."""
        import threading
        import time

        progress_calls: list[tuple[int, int]] = []
        lock = threading.Lock()

        def on_progress(current: int, total: int, action: str, name: str) -> None:
            with lock:
                progress_calls.append((current, total))

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            time.sleep(0.02)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            plan, errors = rb.prepare(
                self.xml_path, "Untitled Intelligent List", self.wav_dir, self.output
            )
            self.assertEqual(errors, [])
            assert plan is not None
            total = len(plan.unique)
            stats = rb.convert_unique(
                plan, force=False, progress=False, on_progress=on_progress
            )

        self.assertEqual(stats.converted, total)
        self.assertEqual(sorted(c for c, _t in progress_calls), list(range(1, total + 1)))

    def test_convert_unique_continues_after_encode_error_and_returns_errors(
        self,
    ) -> None:
        """Given one encode failure: When convert_unique runs: Then successes
        convert, failed dest is absent, and stats.errors lists the boom (no raise)."""
        fail_name = "Revelation.flac"

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            if source.name == fail_name:
                raise rb.CliError(f"boom for {source.name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            plan, errors = rb.prepare(
                self.xml_path, "Untitled Intelligent List", self.wav_dir, self.output
            )
            self.assertEqual(errors, [])
            assert plan is not None
            self.assertEqual(len(plan.unique), 3)
            stats = rb.convert_unique(plan, force=False, progress=False)

        self.assertEqual(stats.converted, 2)
        self.assertTrue(plan.unique[0].dest_path.exists())
        self.assertFalse(plan.unique[1].dest_path.exists())
        self.assertTrue(plan.unique[2].dest_path.exists())
        self.assertTrue(stats.errors)
        joined = "\n".join(stats.errors)
        self.assertIn("boom for", joined)
        self.assertIn(plan.unique[1].source_path.name, joined)

    def test_apply_xml_excludes_exists_but_failed_using_success_set(self) -> None:
        """Given a leftover dest on disk for a failed encode: When apply_xml
        uses the success set: Then that track is omitted from the collection."""
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(rb, "is_cdj_safe_wav", return_value=False):
            plan, errors = rb.prepare(
                self.xml_path, "Untitled Intelligent List", self.wav_dir, self.output
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertGreaterEqual(len(plan.unique), 2)
        ok, failed = plan.unique[0], plan.unique[1]
        ok.dest_path.parent.mkdir(parents=True, exist_ok=True)
        ok.dest_path.write_bytes(b"RIFF")
        failed.dest_path.parent.mkdir(parents=True, exist_ok=True)
        failed.dest_path.write_bytes(b"RIFF")  # leftover on disk
        success = {(rb.source_key(ok.source_path), "wav")}
        with patch.object(
            xml_output, "probe_dest_tech", return_value=("1", "1411", "44100")
        ):
            rb.apply_xml(plan, success)
        locations = [
            t.get("Location") for t in plan.output_root.findall("COLLECTION/TRACK")
        ]
        self.assertIn(ok.dest_location, locations)
        self.assertNotIn(
            failed.dest_location,
            locations,
            "failed encode with leftover file must stay out of import XML",
        )

    def test_partial_encode_main_writes_success_xml_and_exits_nonzero(
        self,
    ) -> None:
        """Given one encode failure among three: When main converts: Then exit
        is nonzero and import XML includes only tracks whose dest files exist."""
        fail_name = "Revelation.flac"
        failed_dest: list[Path] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            if source.name == fail_name:
                failed_dest.append(dest)
                raise rb.CliError(f"boom for {source.name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
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

        self.assertNotEqual(rc, 0)
        self.assertTrue(self.output.is_file())
        self.assertTrue(failed_dest)
        self.assertFalse(failed_dest[0].exists())
        out = ET.parse(self.output).getroot()
        tracks = out.findall("COLLECTION/TRACK")
        self.assertEqual(len(tracks), 2)
        locations = [t.get("Location", "") for t in tracks]
        self.assertNotIn(rb.encode_location(failed_dest[0]), locations)
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")
        self.assertEqual(len(pl), 1)
        self.assertEqual(len(pl[0].findall("TRACK")), 2)

    def test_run_ffmpeg_kills_process_when_cancel_event_set(self) -> None:
        import threading

        cancel = threading.Event()
        cancel.set()
        killed: list[bool] = []
        communicated: list[bool] = []

        class FakeProc(_HangProc):
            def kill(self) -> None:
                killed.append(True)
                super().kill()

            def communicate(self) -> tuple[str, str]:
                communicated.append(True)
                return super().communicate()

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.flac"
            dest = Path(tmp) / "out.wav"
            src.write_bytes(b"flac")
            dest.write_bytes(b"partial")
            with patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
            ), patch.object(
                ffmpeg_tools, "ffmpeg_supports_soxr", return_value=False
            ), patch.object(encode.subprocess, "Popen", FakeProc), patch.object(
                ffmpeg_tools.time, "sleep", lambda _s: None
            ):
                with self.assertRaises(rb.CancelledError):
                    convert_plan.run_ffmpeg(
                        src, dest, "pcm_s16le", force=True, cancel_event=cancel
                    )
            self.assertTrue(killed)
            self.assertTrue(communicated)
            self.assertEqual(dest.read_bytes(), b"partial")
            leftover = [
                p
                for p in dest.parent.iterdir()
                if p.name != dest.name and p.name != src.name
            ]
            self.assertEqual(leftover, [])

    def test_run_ffprobe_kills_process_when_cancel_event_set(self) -> None:
        """Given cancel_event already set: When run_ffprobe runs: Then the
        ffprobe process is killed and CancelledError is raised."""
        import threading

        cancel = threading.Event()
        cancel.set()
        killed: list[bool] = []

        class FakeProc(_HangProc):
            def kill(self) -> None:
                killed.append(True)
                super().kill()

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.flac"
            src.write_bytes(b"flac")
            with patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffprobe"
            ), patch.object(
                ffmpeg_tools.subprocess, "Popen", FakeProc
            ), patch.object(ffmpeg_tools.time, "sleep", lambda _s: None):
                with self.assertRaises(rb.CancelledError):
                    ffmpeg_tools.run_ffprobe(src, cancel_event=cancel)
            self.assertTrue(killed)

    def test_convert_unique_copy_wav_cancel_mid_copy_leaves_no_final_dest(
        self,
    ) -> None:
        """Given copy_wav and cancel mid-copy: When convert_unique returns:
        Then the final dest must not exist (no incomplete final file)."""
        import threading
        import xml.etree.ElementTree as ET

        cancel = threading.Event()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.wav"
            dest = root / "WAV" / "P" / "out.wav"
            dest.parent.mkdir(parents=True)
            src.write_bytes(b"RIFF" + b"\x00" * 40)
            el = ET.Element(
                "TRACK", {"TrackID": "1", "Location": rb.encode_location(src)}
            )
            item = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=rb.encode_location(dest),
                dest_name=dest.name,
                codec=None,
                copy_wav=True,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
            )
            plan = rb.Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                wav_dir=root / "WAV",
                playlist_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
            )

            reads = {"n": 0}
            real_open = open

            def open_side_effect(path, mode="r", *a, **k):
                handle = real_open(path, mode, *a, **k)
                if "rb" in mode and Path(path).resolve() == src.resolve():
                    inner = handle.read

                    def read_and_cancel(size: int = -1) -> bytes:
                        out = inner(size)
                        reads["n"] += 1
                        if reads["n"] >= 1:
                            cancel.set()
                        return out

                    handle.read = read_and_cancel  # type: ignore[method-assign]
                return handle

            with patch.object(encode, "_COPY_CHUNK_SIZE", 8), patch(
                "builtins.open", side_effect=open_side_effect
            ), patch.object(convert_plan, "CONVERT_WORKERS", 1), patch.object(
                convert_plan, "is_cdj_safe_wav", return_value=False
            ):
                rb.convert_unique(
                    plan, force=False, progress=False, cancel_event=cancel
                )

            self.assertFalse(
                dest.exists(),
                "cancel mid-copy must not leave a partial final dest",
            )

    def test_run_ffmpeg_command_suppresses_progress_stats(self) -> None:
        captured: list[list[str]] = []

        class FakeProc:
            def __init__(self, cmd: list[str], *_a: object, **_k: object) -> None:
                captured.append(list(cmd))
                self.returncode = 0

            def poll(self) -> int | None:
                return self.returncode

            def communicate(self) -> tuple[str, str]:
                return "", ""

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.flac"
            dest = Path(tmp) / "out.wav"
            src.write_bytes(b"flac")
            with patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
            ), patch.object(
                ffmpeg_tools, "ffmpeg_supports_soxr", return_value=False
            ), patch.object(
                encode.subprocess, "Popen", FakeProc
            ), patch.object(encode, "is_cdj_safe_wav", return_value=True):
                convert_plan.run_ffmpeg(src, dest, "pcm_s16le", force=True)

        self.assertEqual(len(captured), 1)
        cmd = captured[0]
        # Output path is a temp under dest.parent (not final dest).
        before_out = cmd[:-1]
        self.assertIn("-nostats", before_out)
        self.assertIn("-loglevel", before_out)
        self.assertEqual(before_out[before_out.index("-loglevel") + 1], "error")

    def test_prepare_stops_probing_when_cancel_event_set(self) -> None:
        import threading

        cancel = threading.Event()
        probed: list[Path] = []

        def probe_and_cancel(path: Path, **_kwargs: object) -> dict:
            probed.append(path)
            cancel.set()
            return self._probe(path)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe_and_cancel
        ), patch.object(convert_plan, "CONVERT_WORKERS", 1):
            plan, errors = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
                cancel_event=cancel,
            )
        self.assertEqual(len(probed), 1)
        self.assertIsNone(plan)
        self.assertEqual(errors, [])

    def test_prepare_does_not_wait_out_leftover_probes_after_cancel(self) -> None:
        """Given CONVERT_WORKERS>1 and cancel mid-probe: When prepare returns:
        Then it does not wait for a still-running leftover probe."""
        import threading
        import time

        cancel = threading.Event()
        release_slow = threading.Event()
        probed: list[Path] = []
        lock = threading.Lock()
        both_inside = threading.Barrier(2)

        def probe_side_effect(path: Path, **_kwargs: object) -> dict:
            with lock:
                probed.append(path)
                n = len(probed)
            # Ensure two probes are in-flight before either finishes.
            both_inside.wait(timeout=5)
            if n == 1:
                cancel.set()
                return self._probe(path)
            # Leftover in-flight probe: block until released (must not block prepare).
            if not release_slow.wait(timeout=30):
                raise AssertionError("leftover probe was never released")
            return self._probe(path)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe_side_effect
        ), patch.object(convert_plan, "CONVERT_WORKERS", 2):
            t0 = time.monotonic()
            plan, errors = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
                cancel_event=cancel,
            )
            elapsed = time.monotonic() - t0
        release_slow.set()
        self.assertIsNone(plan)
        self.assertEqual(errors, [])
        self.assertLess(
            elapsed,
            2.0,
            f"prepare waited {elapsed:.2f}s for leftover probe after cancel",
        )

    def test_prepare_probes_unique_tracks_concurrently(self) -> None:
        """Given CONVERT_WORKERS>1: When prepare probes unique tracks:
        Then at least two run_ffprobe calls overlap (barrier of 2 completes)."""
        import threading

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        barrier_slots = 0
        overlapped = threading.Event()

        def probe_with_overlap(path: Path, **_kwargs: object) -> dict:
            nonlocal barrier_slots
            join = False
            with lock:
                if barrier_slots < 2:
                    barrier_slots += 1
                    join = True
            if join:
                try:
                    barrier.wait(timeout=1.0)
                    overlapped.set()
                except threading.BrokenBarrierError:
                    pass
            return self._probe(path)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe_with_overlap
        ), patch.object(convert_plan, "CONVERT_WORKERS", 2):
            plan, errors = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 3)
        self.assertTrue(
            overlapped.is_set(),
            "expected at least two run_ffprobe calls to overlap under CONVERT_WORKERS>1",
        )

    def test_prepare_preserves_unique_order_after_parallel_probe(self) -> None:
        """Given distinct probe results per source: When prepare runs with
        CONVERT_WORKERS>1: Then plan.unique bit_depth/sample_rate/codec stay in
        playlist order (not completion order)."""

        def probe_by_source(path: Path, **_kwargs: object) -> dict:
            if path.name == "07 - Bestial.flac":
                return flac_probe(16)
            if path.name == "Revelation.flac":
                return flac_probe(24)
            if path.name == "Movement.flac":
                return {
                    "format": {"format_name": "flac"},
                    "streams": [
                        {
                            "codec_name": "flac",
                            "sample_fmt": "s32",
                            "sample_rate": "96000",
                            "channels": 2,
                            "bits_per_raw_sample": "24",
                        }
                    ],
                }
            return self._probe(path)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe_by_source
        ), patch.object(convert_plan, "CONVERT_WORKERS", 3):
            plan, errors = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
                max_bit_depth=24,
                max_sample_rate=48000,
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(
            [t.source_path.name for t in plan.unique],
            ["07 - Bestial.flac", "Revelation.flac", "Movement.flac"],
        )
        self.assertEqual(
            [(t.bit_depth, t.sample_rate, t.codec) for t in plan.unique],
            [
                (16, 44100, "pcm_s16le"),
                (24, 44100, "pcm_s24le"),
                (24, 48000, "pcm_s24le"),
            ],
        )

    def test_prepare_reports_progress_while_probing(self) -> None:
        progress_calls: list[tuple[int, int, str, str]] = []

        def on_progress(current: int, total: int, action: str, name: str) -> None:
            progress_calls.append((current, total, action, name))

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
                on_progress=on_progress,
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(progress_calls), 3)
        self.assertEqual([c[0] for c in progress_calls], [1, 2, 3])
        self.assertTrue(all(c[1] == 3 for c in progress_calls))
        self.assertTrue(all(c[2] == "prepare" for c in progress_calls))



if __name__ == "__main__":
    unittest.main()
