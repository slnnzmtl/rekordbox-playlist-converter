#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
import tempfile
import unicodedata
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import rb_playlist_to_wav as rb
import convert_plan
import ffmpeg_tools

FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="219211420" Name="Bestial" Artist="ABSL" Composer=""
           Album="It's just a bad dream" Grouping="" Genre="Electronic"
           Kind="FLAC File" Size="40888414" TotalTime="328" DiscNumber="0"
           TrackNumber="7" Year="2023" AverageBpm="148.00" DateAdded="2024-06-14"
           BitRate="0" SampleRate="44100" Comments="exystence.net" PlayCount="19"
           Rating="51" Location="{loc_a}" Remixer="" Tonality="Bbm"
           Label="Mama told ya" Mix="" Colour="0xFF0000">
      <TEMPO Inizio="0.027" Bpm="148.00" Metro="4/4" Battito="1"/>
      <POSITION_MARK Name="cue" Type="0" Start="0.027" Num="-1" Red="255" Green="0" Blue="0"/>
      <EXTRA Foo="bar"/>
    </TRACK>
    <TRACK TrackID="58834508" Name="Revelation" Artist="Shogan" Composer=""
           Album="Hits" Grouping="" Genre="Trance" Kind="FLAC File" Size="1"
           TotalTime="100" DiscNumber="0" TrackNumber="1" Year="2020"
           AverageBpm="140.00" DateAdded="2024-12-31" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="0"
           Location="{loc_b}" Remixer="" Tonality="Bm" Label="" Mix="">
      <TEMPO Inizio="0.000" Bpm="140.00" Metro="4/4" Battito="1"/>
    </TRACK>
    <TRACK TrackID="115068759" Name="Movement" Artist="Quantum" Composer=""
           Album="Hits" Grouping="" Genre="Trance" Kind="FLAC File" Size="1"
           TotalTime="100" DiscNumber="0" TrackNumber="18" Year="2020"
           AverageBpm="145.00" DateAdded="2024-12-31" BitRate="0"
           SampleRate="44100" Comments="" PlayCount="0" Rating="0"
           Location="{loc_c}" Remixer="" Tonality="G" Label="" Mix="">
      <TEMPO Inizio="0.413" Bpm="145.00" Metro="4/4" Battito="2"/>
    </TRACK>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Intelligent playlists" Type="0" Count="1">
        <NODE Name="Untitled Intelligent List" Type="1" KeyType="0" Entries="3">
          <TRACK Key="219211420"/>
          <TRACK Key="58834508"/>
          <TRACK Key="115068759"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


def flac_probe(bits: int = 24) -> dict:
    fmt = "s32" if bits == 24 else "s16"
    return {
        "format": {"format_name": "flac"},
        "streams": [
            {
                "codec_name": "flac",
                "sample_fmt": fmt,
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": str(bits),
            }
        ],
    }


def wav_probe(bits: int = 24) -> dict:
    codec = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}[bits]
    fmt = "s16" if bits == 16 else "s32"
    return {
        "format": {"format_name": "wav"},
        "streams": [
            {
                "codec_name": codec,
                "sample_fmt": fmt,
                "sample_rate": "44100",
                "channels": 2,
                "bits_per_raw_sample": str(bits),
            }
        ],
    }


def write_flac(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC")


class CodecMapTests(unittest.TestCase):
    def test_16_24_32_float(self) -> None:
        self.assertEqual(
            rb.pcm_codec_for_stream({"sample_fmt": "s16", "bits_per_raw_sample": "16"}),
            "pcm_s16le",
        )
        self.assertEqual(
            rb.pcm_codec_for_stream({"sample_fmt": "s32", "bits_per_raw_sample": "24"}),
            "pcm_s24le",
        )
        self.assertEqual(
            rb.pcm_codec_for_stream({"sample_fmt": "s32", "bits_per_raw_sample": "32"}),
            "pcm_s32le",
        )
        self.assertEqual(
            rb.pcm_codec_for_stream({"sample_fmt": "fltp"}),
            "pcm_f32le",
        )

    def test_unknown_depth_fails(self) -> None:
        with self.assertRaises(rb.CliError):
            rb.pcm_codec_for_stream({"sample_fmt": "s32"})
        with self.assertRaises(rb.CliError):
            rb.pcm_codec_for_stream({"sample_fmt": "u8", "bits_per_raw_sample": "8"})


class CollisionKeyTests(unittest.TestCase):
    def test_case_and_nfd(self) -> None:
        self.assertEqual(rb.collision_key("Intro.wav"), rb.collision_key("intro.wav"))
        nfc = unicodedata.normalize("NFC", "café.wav")
        nfd = unicodedata.normalize("NFD", "café.wav")
        self.assertNotEqual(nfc, nfd)
        self.assertEqual(rb.collision_key(nfc), rb.collision_key(nfd))


class XmlFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.music = self.root / "music"
        self.a = self.music / "It's just a bad dream" / "07 - Bestial.flac"
        self.b = self.music / "Shogan" / "Revelation.flac"
        self.c = self.music / "Quantum" / "Movement.flac"
        for p in (self.a, self.b, self.c):
            write_flac(p)
        self.xml_path = self.root / "collection.xml"
        xml = FIXTURE.format(
            loc_a=rb.encode_location(self.a),
            loc_b=rb.encode_location(self.b),
            loc_c=rb.encode_location(self.c),
        )
        self.xml_path.write_text(xml, encoding="utf-8")
        self.wav_dir = self.root / "WAV"
        self.output = self.root / "out.xml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _probe(self, path: Path) -> dict:
        if path.suffix.lower() == ".wav":
            return wav_probe()
        return flac_probe()

    def test_recursive_playlist_lookup(self) -> None:
        root = rb.load_dj_playlists(self.xml_path)
        found = rb.find_playlists_by_name(root, "Untitled Intelligent List")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].get("Entries"), "3")
        self.assertEqual(rb.find_playlists_by_name(root, "missing"), [])

    def test_duplicate_playlist_name(self) -> None:
        root = rb.load_dj_playlists(self.xml_path)
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
        root = rb.load_dj_playlists(self.xml_path)
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
        root = rb.load_dj_playlists(self.xml_path)
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

    def test_convert_unique_stops_remaining_tracks_when_cancel_event_set(self) -> None:
        """Cancel during encode: drop in-flight dest, skip the rest."""
        import threading

        cancel = threading.Event()

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"partial")
            cancel.set()
            dest.unlink(missing_ok=True)
            raise rb.CancelledError(f"conversion cancelled for {source}")

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
            stats = rb.convert_unique(
                plan, force=False, progress=False, cancel_event=cancel
            )

        self.assertEqual(stats.converted, 0)
        for item in plan.unique:
            self.assertFalse(item.dest_path.exists())

    def test_convert_unique_progress_is_completed_count(self) -> None:
        """Progress current is completed items and never exceeds total."""
        import threading
        import time

        progress_calls: list[tuple[int, int, str, str]] = []
        lock = threading.Lock()

        def on_progress(current: int, total: int, action: str, name: str) -> None:
            with lock:
                progress_calls.append((current, total, action, name))

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
        self.assertEqual(len(progress_calls), total)
        currents = [c[0] for c in progress_calls]
        self.assertEqual(sorted(currents), list(range(1, total + 1)))
        self.assertTrue(all(c[1] == total for c in progress_calls))
        self.assertLessEqual(max(currents), total)

    def test_convert_unique_cancel_keeps_completed_and_does_not_raise(self) -> None:
        """Cancel mid-run returns stats without raising; completed dest kept."""
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

            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(run)
                self.assertTrue(first_done.wait(timeout=5))
                cancel.set()
                stats = fut.result(timeout=10)

        self.assertEqual(stats.converted, 1)
        self.assertTrue(plan.unique[0].dest_path.exists())
        self.assertFalse(plan.unique[-1].dest_path.exists())

    def test_convert_unique_overlaps_at_least_two_encodes(self) -> None:
        """At least two run_ffmpeg calls must be in flight at once."""
        import threading

        started = threading.Barrier(2, timeout=5)
        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            try:
                started.wait()
            except threading.BrokenBarrierError:
                pass
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")
            with lock:
                in_flight -= 1

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
            self.assertGreaterEqual(len(plan.unique), 2)
            stats = rb.convert_unique(plan, force=False, progress=False)

        self.assertGreaterEqual(peak, 2)
        self.assertEqual(stats.converted, len(plan.unique))

    def test_convert_unique_never_exceeds_worker_cap(self) -> None:
        """In-flight encodes stay at or under CONVERT_WORKERS."""
        import threading
        import time

        in_flight = 0
        peak = 0
        lock = threading.Lock()
        workers = 2

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.05)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")
            with lock:
                in_flight -= 1

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ), patch.object(convert_plan, "CONVERT_WORKERS", workers):
            plan, errors = rb.prepare(
                self.xml_path, "Untitled Intelligent List", self.wav_dir, self.output
            )
            self.assertEqual(errors, [])
            assert plan is not None
            stats = rb.convert_unique(plan, force=False, progress=False)

        self.assertLessEqual(peak, workers)
        self.assertEqual(stats.converted, len(plan.unique))

    def test_convert_unique_continues_after_encode_error_and_raises_all(self) -> None:
        """One encode failure: other tracks still convert; CliError lists errors."""
        call_n = {"n": 0}

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            call_n["n"] += 1
            if call_n["n"] == 2:
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
            with self.assertRaises(rb.CliError) as ctx:
                rb.convert_unique(plan, force=False, progress=False)

        self.assertEqual(plan.unique[0].dest_path.exists(), True)
        self.assertEqual(plan.unique[1].dest_path.exists(), False)
        self.assertEqual(plan.unique[2].dest_path.exists(), True)
        self.assertIn("boom for", str(ctx.exception))
        self.assertIn(plan.unique[1].source_path.name, str(ctx.exception))

    def test_convert_unique_collects_oserror_from_copy_path(self) -> None:
        """Non-CliError failures (e.g. copy OSError) are collected too."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "safe.wav"
            src.write_bytes(b"RIFF")
            dest = root / "out.wav"
            el = ET.Element("TRACK", {"Name": "Song"})
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
                wav_dir=root,
                playlist_dir=root,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
            )
            with patch.object(
                convert_plan.shutil, "copy2", side_effect=OSError("disk full")
            ), patch.object(convert_plan, "is_cdj_safe_wav", return_value=False):
                with self.assertRaises(rb.CliError) as ctx:
                    rb.convert_unique(plan, force=False, progress=False)
            self.assertIn("disk full", str(ctx.exception))
            self.assertFalse(dest.exists())

    def test_run_ffmpeg_kills_process_when_cancel_event_set(self) -> None:
        import threading

        cancel = threading.Event()
        cancel.set()
        killed: list[bool] = []

        class FakeProc:
            def __init__(self, *_a: object, **_k: object) -> None:
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:
                killed.append(True)
                self.returncode = -9

            def wait(self, timeout: float | None = None) -> int:
                return self.returncode if self.returncode is not None else -9

            def communicate(self) -> tuple[str, str]:
                return "", ""

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.flac"
            dest = Path(tmp) / "out.wav"
            src.write_bytes(b"flac")
            dest.write_bytes(b"partial")
            with patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
            ), patch.object(
                ffmpeg_tools, "ffmpeg_supports_soxr", return_value=False
            ), patch.object(convert_plan.subprocess, "Popen", FakeProc), patch.object(
                convert_plan.time, "sleep", lambda _s: None
            ):
                with self.assertRaises(rb.CancelledError):
                    convert_plan.run_ffmpeg(
                        src, dest, "pcm_s16le", force=True, cancel_event=cancel
                    )
            self.assertTrue(killed)
            self.assertFalse(dest.exists())

    def test_prepare_stops_probing_when_cancel_event_set(self) -> None:
        import threading

        cancel = threading.Event()
        probed: list[Path] = []

        def probe_and_cancel(path: Path) -> dict:
            probed.append(path)
            cancel.set()
            return self._probe(path)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe_and_cancel
        ):
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

    def test_missing_source_file_does_not_abort_convert(self) -> None:
        self.c.unlink()

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
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
        self.assertEqual(rc, 0)
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 2)
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")
        self.assertEqual(len(pl), 1)
        self.assertEqual([t.get("Key") for t in pl[0].findall("TRACK")], ["1", "2"])

    def test_missing_collection_key(self) -> None:
        root = rb.load_dj_playlists(self.xml_path)
        node = rb.find_playlists_by_name(root, "Untitled Intelligent List")[0]
        ET.SubElement(node, "TRACK", {"Key": "999"})
        bad = self.root / "missing-key.xml"
        ET.ElementTree(root).write(bad, encoding="UTF-8", xml_declaration=True)
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            _, errors = rb.prepare(bad, "Untitled Intelligent List", self.wav_dir, self.output)
        self.assertTrue(any("missing collection track" in e for e in errors))

    def test_invalid_url(self) -> None:
        root = rb.load_dj_playlists(self.xml_path)
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

    def test_collisions_case_and_nfd(self) -> None:
        intro = self.music / "one" / "Intro.flac"
        intro2 = self.music / "two" / "intro.flac"
        cafe_nfc = self.music / "n1" / (unicodedata.normalize("NFC", "café") + ".flac")
        cafe_nfd = self.music / "n2" / (unicodedata.normalize("NFD", "café") + ".flac")
        for p in (intro, intro2, cafe_nfc, cafe_nfd):
            write_flac(p)
        extra = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="4">
    <TRACK TrackID="1" Name="a" Location="{rb.encode_location(intro)}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="b" Location="{rb.encode_location(intro2)}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="c" Location="{rb.encode_location(cafe_nfc)}" Kind="FLAC File"/>
    <TRACK TrackID="4" Name="d" Location="{rb.encode_location(cafe_nfd)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Clash" Type="1" KeyType="0" Entries="4">
        <TRACK Key="1"/><TRACK Key="2"/><TRACK Key="3"/><TRACK Key="4"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "clash.xml"
        path.write_text(extra, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            _, errors = rb.prepare(path, "Clash", self.wav_dir, self.output)
        joined = "\n".join(errors)
        self.assertIn("Filename collision", joined)
        self.assertIn("Intro.wav", joined)
        self.assertTrue("café.wav" in joined or "cafe" in joined.casefold())

    def test_unknown_fields_preserved_and_ids_start_at_1(self) -> None:
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
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
        self.assertEqual(rc, 0)
        out = ET.parse(self.output).getroot()
        tracks = out.findall("COLLECTION/TRACK")
        self.assertEqual(len(tracks), 3)
        self.assertEqual([t.get("TrackID") for t in tracks], ["1", "2", "3"])
        first = tracks[0]
        self.assertEqual(first.get("Colour"), "0xFF0000")
        self.assertEqual(first.get("Rating"), "51")
        self.assertEqual(first.get("Kind"), "WAV File")
        loc = first.get("Location") or ""
        self.assertIn("/WAV/Untitled%20Intelligent%20List/", loc)
        self.assertTrue((self.wav_dir / "Untitled Intelligent List" / "07 - Bestial.wav").is_file())
        extra = first.find("EXTRA")
        self.assertIsNotNone(extra)
        assert extra is not None
        self.assertEqual(extra.get("Foo"), "bar")
        self.assertIsNotNone(first.find("TEMPO"))
        self.assertIsNotNone(first.find("POSITION_MARK"))
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")
        self.assertEqual(len(pl), 1)
        self.assertEqual([t.get("Key") for t in pl[0].findall("TRACK")], ["1", "2", "3"])

    def test_prepare_all_then_apply_keeps_every_playlist(self) -> None:
        """GUI prepares every playlist before writing; trees must be shared."""
        src = rb.load_dj_playlists(self.xml_path)
        playlists_root = src.find("PLAYLISTS/NODE")
        assert playlists_root is not None
        morning = ET.SubElement(
            playlists_root,
            "NODE",
            {"Name": "Morning", "Type": "1", "KeyType": "0", "Entries": "1"},
        )
        ET.SubElement(morning, "TRACK", {"Key": "219211420"})
        playlists_root.set("Count", "2")
        ET.ElementTree(src).write(self.xml_path, encoding="UTF-8", xml_declaration=True)

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            plan_a, errors_a = rb.prepare(
                self.xml_path, "Untitled Intelligent List", self.wav_dir, self.output
            )
            plan_b, errors_b = rb.prepare(
                self.xml_path, "Morning", self.wav_dir, self.output
            )
            self.assertEqual(errors_a, [])
            self.assertEqual(errors_b, [])
            assert plan_a is not None and plan_b is not None
            self.assertIsNot(plan_a.output_root, plan_b.output_root)
            plans = [plan_a, plan_b]
            rb.share_output_root(plans)
            self.assertIs(plan_a.output_root, plan_b.output_root)
            for plan in plans:
                rb.convert_unique(plan, force=False, progress=False)
                rb.apply_xml(plan)
                rb.atomic_write_xml(plan.output_root, plan.output)

        out = ET.parse(self.output).getroot()
        names = sorted(name for _folder, name, _node in rb.iter_playlists(out))
        self.assertEqual(names, ["Morning [WAV]", "Untitled Intelligent List [WAV]"])
        root_node = out.find("PLAYLISTS/NODE")
        assert root_node is not None
        self.assertEqual(root_node.get("Count"), "2")
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 4)
        morning_pl = rb.find_playlists_by_name(out, "Morning [WAV]")[0]
        self.assertEqual(len(morning_pl.findall("TRACK")), 1)

    def test_location_reuse_and_rerun_extends_playlist(self) -> None:
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        patches = (
            patch.object(ffmpeg_tools, "require_tools", return_value=[]),
            patch.object(ffmpeg_tools, "run_ffprobe", side_effect=self._probe),
            patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg),
            patch.object(convert_plan, "is_cdj_safe_wav", return_value=False),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            self.assertEqual(
                rb.main(
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
                ),
                0,
            )
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)

        # Drop last track from source playlist; re-run must keep existing WAV playlist entries.
        src = ET.parse(self.xml_path).getroot()
        node = rb.find_playlists_by_name(src, "Untitled Intelligent List")[0]
        for child in list(node):
            node.remove(child)
        ET.SubElement(node, "TRACK", {"Key": "219211420"})
        ET.SubElement(node, "TRACK", {"Key": "58834508"})
        node.set("Entries", "2")
        slim = self.root / "slim.xml"
        ET.ElementTree(src).write(slim, encoding="UTF-8", xml_declaration=True)

        with patches[0], patches[1], patches[2], patches[3]:
            self.assertEqual(
                rb.main(
                    [
                        "--xml",
                        str(slim),
                        "--playlist",
                        "Untitled Intelligent List",
                        "--wav-dir",
                        str(self.wav_dir),
                        "--output",
                        str(self.output),
                    ]
                ),
                0,
            )
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")[0]
        self.assertEqual([t.get("Key") for t in pl.findall("TRACK")], ["1", "2", "3"])

        # Add a fourth source track; re-run appends one collection TRACK and one playlist entry.
        d = self.music / "New" / "Added.flac"
        write_flac(d)
        src = ET.parse(self.xml_path).getroot()
        collection = src.find("COLLECTION")
        assert collection is not None
        ET.SubElement(
            collection,
            "TRACK",
            {
                "TrackID": "42",
                "Name": "Added",
                "Kind": "FLAC File",
                "Location": rb.encode_location(d),
                "BitRate": "0",
                "SampleRate": "44100",
            },
        )
        node = rb.find_playlists_by_name(src, "Untitled Intelligent List")[0]
        ET.SubElement(node, "TRACK", {"Key": "42"})
        grown = self.root / "grown.xml"
        ET.ElementTree(src).write(grown, encoding="UTF-8", xml_declaration=True)
        with patches[0], patches[1], patches[2], patches[3]:
            self.assertEqual(
                rb.main(
                    [
                        "--xml",
                        str(grown),
                        "--playlist",
                        "Untitled Intelligent List",
                        "--wav-dir",
                        str(self.wav_dir),
                        "--output",
                        str(self.output),
                    ]
                ),
                0,
            )
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 4)
        ids = [t.get("TrackID") for t in out.findall("COLLECTION/TRACK")]
        self.assertEqual(ids[:3], ["1", "2", "3"])
        self.assertEqual(ids[3], "4")
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")[0]
        self.assertEqual([t.get("Key") for t in pl.findall("TRACK")], ["1", "2", "3", "4"])

    def test_invalid_existing_output_not_clobbered(self) -> None:
        self.output.write_text("not a rekordbox collection", encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
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
        self.assertEqual(rc, 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "not a rekordbox collection")

    def test_dry_run_writes_nothing(self) -> None:
        buf = io.StringIO()
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch("sys.stdout", buf):
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
                    "--dry-run",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.wav_dir.exists())
        self.assertIn("Untitled Intelligent List [WAV]", buf.getvalue())

    def test_unsupported_lossy_format_errors_flac_without_depth_ok(self) -> None:
        mp3 = self.music / "x.mp3"
        mp3.write_bytes(b"ID3")
        mystery = self.music / "odd.flac"
        write_flac(mystery)

        def probe(path: Path) -> dict:
            if path.suffix == ".mp3":
                return {
                    "format": {"format_name": "mp3"},
                    "streams": [
                        {
                            "codec_name": "mp3",
                            "sample_fmt": "fltp",
                            "sample_rate": "44100",
                            "channels": 2,
                        }
                    ],
                }
            if path.name == "odd.flac":
                return {
                    "format": {"format_name": "flac"},
                    "streams": [
                        {
                            "codec_name": "flac",
                            "sample_fmt": "s32",
                            "sample_rate": "44100",
                            "channels": 2,
                        }
                    ],
                }
            return self._probe(path)

        extra = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="mp3" Location="{rb.encode_location(mp3)}" Kind="MP3 File"/>
    <TRACK TrackID="2" Name="odd" Location="{rb.encode_location(mystery)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Bad" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/><TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "badfmt.xml"
        path.write_text(extra, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe
        ):
            plan, errors = rb.prepare(path, "Bad", self.wav_dir, self.output)
        joined = "\n".join(errors)
        self.assertIn("unsupported format", joined)
        self.assertNotIn("unknown bit depth", joined)
        # FLAC without bits_per_raw_sample still plans under the default ceiling.
        assert plan is not None
        flac_items = [t for t in plan.unique if t.source_path == mystery]
        self.assertEqual(len(flac_items), 1)
        self.assertEqual(flac_items[0].codec, "pcm_s24le")
        self.assertEqual(flac_items[0].bit_depth, 24)
        self.assertEqual(flac_items[0].sample_rate, 44100)
        self.assertFalse(flac_items[0].copy_wav)
        self.assertEqual(plan.max_bit_depth, 24)
        self.assertEqual(plan.max_sample_rate, 48000)

    def test_playlist_dir_name_sanitizes_separators(self) -> None:
        self.assertEqual(rb.playlist_dir_name("Dark forest"), "Dark forest")
        self.assertEqual(rb.playlist_dir_name("a/b\\c"), "a_b_c")
        with self.assertRaises(rb.CliError):
            rb.playlist_dir_name("..")

    def test_format_invalid_exits(self) -> None:
        with self.assertRaises(SystemExit):
            rb.parse_args(["--xml", "in.xml", "--playlist", "P", "--format", "mp3"])

    def test_bit_depth_invalid_exits(self) -> None:
        with self.assertRaises(SystemExit):
            rb.parse_args(
                ["--xml", "in.xml", "--playlist", "P", "--bit-depth", "32"]
            )

    def test_sample_rate_invalid_exits(self) -> None:
        with self.assertRaises(SystemExit):
            rb.parse_args(
                ["--xml", "in.xml", "--playlist", "P", "--sample-rate", "96000"]
            )


class TargetFromStreamTests(unittest.TestCase):
    """Quality is a ceiling: never raise depth or rate above the source."""

    def test_ceiling_table_from_issue(self) -> None:
        cases = [
            # source bits/rate, max bits/rate, expected bits/rate
            (16, 44100, 24, 48000, 16, 44100),
            (24, 44100, 24, 48000, 24, 44100),
            (16, 48000, 24, 48000, 16, 48000),
            (24, 48000, 16, 44100, 16, 44100),
            (24, 96000, 24, 48000, 24, 48000),
            (24, 88200, 24, 48000, 24, 44100),
            (24, 176400, 24, 48000, 24, 44100),
            (16, 22050, 24, 48000, 16, 44100),
        ]
        for src_bits, src_rate, max_bits, max_rate, exp_bits, exp_rate in cases:
            with self.subTest(
                src=(src_bits, src_rate), max_=(max_bits, max_rate)
            ):
                stream = {
                    "sample_fmt": f"s{src_bits}",
                    "bits_per_raw_sample": str(src_bits),
                    "sample_rate": str(src_rate),
                }
                bits, rate = rb.target_from_stream(
                    stream, max_bit_depth=max_bits, max_sample_rate=max_rate
                )
                self.assertEqual((bits, rate), (exp_bits, exp_rate))


class PlanQualityFieldsTests(unittest.TestCase):
    def test_build_plan_stores_selected_and_effective_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "hi.flac"
            write_flac(src)
            xml_path = root / "c.xml"
            xml_path.write_text(
                f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Hi" Location="{rb.encode_location(src)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="P" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
""",
                encoding="utf-8",
            )
            with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
                ffmpeg_tools,
                "run_ffprobe",
                return_value={
                    "streams": [
                        {
                            "codec_name": "flac",
                            "sample_fmt": "s32",
                            "bits_per_raw_sample": "24",
                            "sample_rate": "96000",
                            "channels": "2",
                        }
                    ]
                },
            ):
                plan, errors = rb.prepare(
                    xml_path,
                    "P",
                    root / "out",
                    root / "import.xml",
                    output_format="wav",
                    max_bit_depth=24,
                    max_sample_rate=48000,
                )
            self.assertEqual(errors, [])
            assert plan is not None
            self.assertEqual(plan.output_format, "wav")
            self.assertEqual(plan.max_bit_depth, 24)
            self.assertEqual(plan.max_sample_rate, 48000)
            self.assertEqual(len(plan.unique), 1)
            self.assertEqual(plan.unique[0].bit_depth, 24)
            self.assertEqual(plan.unique[0].sample_rate, 48000)
            self.assertEqual(plan.unique[0].codec, "pcm_s24le")


class WizardHelperTests(unittest.TestCase):
    def test_main_requires_flags_when_non_tty(self) -> None:
        with patch.object(rb.sys.stdin, "isatty", return_value=False):
            rc = rb.main([])
        self.assertEqual(rc, 2)

    def test_tool_path_uses_path_when_not_frozen(self) -> None:
        with patch.object(ffmpeg_tools.sys, "frozen", False, create=True), patch.object(
            ffmpeg_tools.shutil, "which", return_value="/usr/local/bin/ffmpeg"
        ) as which:
            self.assertEqual(ffmpeg_tools.tool_path("ffmpeg"), "/usr/local/bin/ffmpeg")
            which.assert_called_once_with("ffmpeg")

    def test_tool_path_prefers_meipass_when_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meipass = Path(tmp)
            bundled = meipass / "ffmpeg"
            bundled.write_text("")
            bundled.chmod(0o755)
            with patch.object(ffmpeg_tools.sys, "frozen", True, create=True), patch.object(
                ffmpeg_tools.sys, "_MEIPASS", str(meipass), create=True
            ), patch.object(ffmpeg_tools.shutil, "which") as which:
                self.assertEqual(ffmpeg_tools.tool_path("ffmpeg"), str(bundled))
                which.assert_not_called()

    def test_tool_path_falls_back_to_executable_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mac_os = Path(tmp) / "MacOS"
            mac_os.mkdir()
            beside = mac_os / "ffprobe"
            beside.write_text("")
            beside.chmod(0o755)
            fake_exe = mac_os / "Simple Rekordbox Converter"
            fake_exe.write_text("")
            with patch.object(ffmpeg_tools.sys, "frozen", True, create=True), patch.object(
                ffmpeg_tools.sys, "_MEIPASS", str(Path(tmp) / "missing"), create=True
            ), patch.object(ffmpeg_tools.sys, "executable", str(fake_exe)), patch.object(
                ffmpeg_tools.shutil, "which"
            ) as which:
                self.assertEqual(
                    ffmpeg_tools.tool_path("ffprobe"),
                    str(beside.resolve()),
                )
                which.assert_not_called()


class SubprocessTimeoutTests(unittest.TestCase):
    def test_run_ffprobe_maps_timeout_to_cli_error(self) -> None:
        import subprocess

        path = Path("/tmp/track.flac")
        with patch.object(
            ffmpeg_tools, "tool_path", return_value="/bin/ffprobe"
        ), patch.object(
            ffmpeg_tools.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=60),
        ):
            with self.assertRaises(rb.CliError) as ctx:
                ffmpeg_tools.run_ffprobe(path)
        self.assertIn("timed out", str(ctx.exception).lower())
        self.assertIn(str(path), str(ctx.exception))

    def test_run_ffmpeg_maps_timeout_to_cli_error(self) -> None:
        class FakeProc:
            def __init__(self, *_a: object, **_k: object) -> None:
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:
                self.returncode = -9

            def wait(self, timeout: float | None = None) -> int:
                return -9

            def communicate(self) -> tuple[str, str]:
                return "", ""

        src = Path("/tmp/src.flac")
        dest = Path("/tmp/out.wav")
        with patch.object(
            ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
        ), patch.object(
            ffmpeg_tools, "ffmpeg_supports_soxr", return_value=False
        ), patch.object(
            ffmpeg_tools, "FFMPEG_CONVERT_TIMEOUT_S", 0.01
        ), patch.object(convert_plan.subprocess, "Popen", FakeProc), patch.object(
            convert_plan.time, "sleep", lambda _s: None
        ), patch.object(convert_plan.time, "monotonic", side_effect=[0.0, 0.02]):
            with self.assertRaises(rb.CliError) as ctx:
                convert_plan.run_ffmpeg(src, dest, "pcm_s16le", force=True)
        self.assertIn("timed out", str(ctx.exception).lower())
        self.assertIn(str(src), str(ctx.exception))

    def test_extract_cover_jpeg_returns_none_on_timeout(self) -> None:
        import subprocess
        import cdj_aiff

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "song.aiff"
            src.write_bytes(b"x")
            with patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
            ), patch.object(
                cdj_aiff.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30),
            ):
                self.assertIsNone(rb.extract_cover_jpeg(src))

    def test_ffmpeg_supports_soxr_false_on_timeout(self) -> None:
        import subprocess

        ffmpeg_tools.ffmpeg_supports_soxr.cache_clear()
        try:
            with patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
            ), patch.object(
                ffmpeg_tools.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=15),
            ):
                self.assertFalse(ffmpeg_tools.ffmpeg_supports_soxr())
        finally:
            ffmpeg_tools.ffmpeg_supports_soxr.cache_clear()


if __name__ == "__main__":
    unittest.main()
