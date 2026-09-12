#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import struct
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
import converter_manifest
import ffmpeg_tools
import xml_output


def write_pcm_wav(
    path: Path,
    *,
    sample_rate: int = 44100,
    channels: int = 2,
    bits: int = 16,
    frames: int = 8,
) -> None:
    """Minimal stereo WAVE_FORMAT_PCM for skip/rebuild tests."""
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    data = b"\x00" * (frames * block_align)
    fmt_payload = struct.pack(
        "<HHIIHH",
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
    )
    body = b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload
    body += b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)

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

    def test_convert_unique_success_set_excludes_encode_failures(self) -> None:
        """Given one encode failure: When convert_unique returns: Then
        stats.succeeded has (source_key, format) for converted tracks only."""
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
            stats = rb.convert_unique(plan, force=False, progress=False)

        failed = next(i for i in plan.unique if i.source_path.name == fail_name)
        ok = [i for i in plan.unique if i.source_path.name != fail_name]
        expected = {(rb.source_key(i.source_path), "wav") for i in ok}
        self.assertEqual(stats.succeeded, expected)
        self.assertNotIn((rb.source_key(failed.source_path), "wav"), stats.succeeded)

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
                communicated.append(True)
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
            self.assertTrue(communicated)
            self.assertEqual(dest.read_bytes(), b"partial")
            leftover = [
                p
                for p in dest.parent.iterdir()
                if p.name != dest.name and p.name != src.name
            ]
            self.assertEqual(leftover, [])

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

            with patch.object(convert_plan, "_COPY_CHUNK_SIZE", 8), patch(
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
                convert_plan.subprocess, "Popen", FakeProc
            ), patch.object(convert_plan, "is_cdj_safe_wav", return_value=True):
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

        def probe_and_cancel(path: Path) -> dict:
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

    def test_prepare_probes_unique_tracks_concurrently(self) -> None:
        """Given CONVERT_WORKERS>1: When prepare probes unique tracks:
        Then at least two run_ffprobe calls overlap (barrier of 2 completes)."""
        import threading

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        barrier_slots = 0
        overlapped = threading.Event()

        def probe_with_overlap(path: Path) -> dict:
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

        def probe_by_source(path: Path) -> dict:
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

    def test_identical_metadata_gets_numbered_suffixes(self) -> None:
        """Given three sources with identical Artist/Album/Name: When prepare
        runs: Then dests are Name.wav, Name (2).wav, Name (3).wav and none are
        dropped for the clash."""
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
           Location="{rb.encode_location(srcs[0])}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(srcs[1])}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(srcs[2])}" Kind="FLAC File"/>
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
        path = self.root / "triple-clash.xml"
        path.write_text(xml, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(path, "Triple", self.wav_dir, self.output)
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 3)
        dests = {
            u.source_path: u.dest_path.relative_to(plan.wav_dir).as_posix()
            for u in plan.unique
        }
        self.assertEqual(dests[srcs[0]], "Same/Hits/Song.wav")
        self.assertEqual(dests[srcs[1]], "Same/Hits/Song (2).wav")
        self.assertEqual(dests[srcs[2]], "Same/Hits/Song (3).wav")

    def test_numbered_dest_stays_stable_when_earlier_source_absent(
        self,
    ) -> None:
        """Given Song.wav and Song (2).wav reserved: When only the (2) source
        remains in the playlist: Then it keeps Song (2).wav (no compact)."""
        first = self.music / "a" / "one.flac"
        second = self.music / "b" / "two.flac"
        for p in (first, second):
            write_flac(p)

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        both = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(first)}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(second)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Both" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/><TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "stable-number.xml"
        path.write_text(both, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            rc1 = rb.main(
                [
                    "--xml",
                    str(path),
                    "--playlist",
                    "Both",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(rc1, 0)
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert_plan.source_key(second)]["wav"]["dest"],
            "Same/Hits/Song (2).wav",
        )

        only_second = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="2" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(second)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="OnlySecond" Type="1" KeyType="0" Entries="1">
        <TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path.write_text(only_second, encoding="utf-8")
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=True
        ):
            plan, errors = rb.prepare(
                path, "OnlySecond", self.wav_dir, self.output
            )
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 1)
        self.assertEqual(
            plan.unique[0].dest_path.relative_to(plan.wav_dir).as_posix(),
            "Same/Hits/Song (2).wav",
        )
        data2 = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data2["tracks"][convert_plan.source_key(second)]["wav"]["dest"],
            "Same/Hits/Song (2).wav",
        )
        self.assertNotEqual(
            plan.unique[0].dest_path.relative_to(plan.wav_dir).as_posix(),
            "Same/Hits/Song.wav",
        )

    def test_unrelated_occupant_forces_numbered_dest_on_first_assign(
        self,
    ) -> None:
        """Given a file already at preferred dest but not in the manifest: When
        convert assigns that source: Then dest is Name (2).ext and the occupant
        file is left intact."""
        src = self.music / "solo" / "track.flac"
        write_flac(src)
        preferred = self.wav_dir / "Same" / "Hits" / "Song.wav"
        preferred.parent.mkdir(parents=True)
        preferred.write_bytes(b"UNRELATED-OCCUPANT")

        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Song" Artist="Same" Album="Hits"
           Location="{rb.encode_location(src)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Occupy" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "unrelated-occupant.xml"
        path.write_text(xml, encoding="utf-8")

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
                    str(path),
                    "--playlist",
                    "Occupy",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(rc, 0)
        numbered = self.wav_dir / "Same" / "Hits" / "Song (2).wav"
        self.assertTrue(numbered.is_file())
        self.assertEqual(preferred.read_bytes(), b"UNRELATED-OCCUPANT")
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert_plan.source_key(src)]["wav"]["dest"],
            "Same/Hits/Song (2).wav",
        )

    def test_collisions_case_and_nfd(self) -> None:
        """Given case and NFD dest twins with different sources: When prepare
        runs: Then each source gets a sticky numbered dest (Name, Name (2), …)
        and unique keeps every source."""
        intro = self.music / "one" / "Intro.flac"
        intro2 = self.music / "two" / "intro.flac"
        cafe_nfc = self.music / "n1" / (unicodedata.normalize("NFC", "café") + ".flac")
        cafe_nfd = self.music / "n2" / (unicodedata.normalize("NFD", "café") + ".flac")
        for p in (intro, intro2, cafe_nfc, cafe_nfd):
            write_flac(p)
        cafe_name_nfc = unicodedata.normalize("NFC", "café")
        cafe_name_nfd = unicodedata.normalize("NFD", "café")
        extra = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="4">
    <TRACK TrackID="1" Name="Intro" Artist="Same" Album="Hits"
           Location="{rb.encode_location(intro)}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="intro" Artist="Same" Album="Hits"
           Location="{rb.encode_location(intro2)}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="{cafe_name_nfc}" Artist="Same" Album="Hits"
           Location="{rb.encode_location(cafe_nfc)}" Kind="FLAC File"/>
    <TRACK TrackID="4" Name="{cafe_name_nfd}" Artist="Same" Album="Hits"
           Location="{rb.encode_location(cafe_nfd)}" Kind="FLAC File"/>
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
            plan, errors = rb.prepare(path, "Clash", self.wav_dir, self.output)
        self.assertEqual(errors, [])
        assert plan is not None
        self.assertEqual(len(plan.unique), 4)
        intro_dests = sorted(
            u.dest_path.relative_to(plan.wav_dir).as_posix()
            for u in plan.unique
            if "intro" in u.dest_name.casefold()
        )
        self.assertEqual(len(intro_dests), 2)
        self.assertEqual(
            {rb.collision_key(d) for d in intro_dests},
            {
                rb.collision_key("Same/Hits/Intro.wav"),
                rb.collision_key("Same/Hits/Intro (2).wav"),
            },
        )
        first_intro = next(u for u in plan.unique if u.source_path == intro)
        self.assertEqual(
            first_intro.dest_path.relative_to(plan.wav_dir).as_posix(),
            "Same/Hits/Intro.wav",
        )
        cafe_dests = sorted(
            u.dest_path.relative_to(plan.wav_dir).as_posix()
            for u in plan.unique
            if "caf" in unicodedata.normalize("NFC", u.dest_name).casefold()
        )
        self.assertEqual(len(cafe_dests), 2)
        self.assertTrue(any(" (2).wav" in d for d in cafe_dests))
        self.assertTrue(
            any(
                rb.collision_key(d) == rb.collision_key("Same/Hits/café.wav")
                for d in cafe_dests
            )
        )
    def test_convert_succeeds_when_filename_collision_gets_numbered_dest(
        self,
    ) -> None:
        """Given a collision pair plus one clean track: When main converts:
        Then exit is 0 and both colliding sources are converted to numbered dests."""
        intro = self.music / "one" / "Intro.flac"
        intro2 = self.music / "two" / "intro.flac"
        clean = self.music / "clean" / "Clean Track.flac"
        for p in (intro, intro2, clean):
            write_flac(p)
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="1" Name="Intro" Artist="Same" Album="Hits"
           Location="{rb.encode_location(intro)}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="intro" Artist="Same" Album="Hits"
           Location="{rb.encode_location(intro2)}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="Clean Track" Artist="Other" Album="Solo"
           Location="{rb.encode_location(clean)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="ClashConvert" Type="1" KeyType="0" Entries="3">
        <TRACK Key="1"/><TRACK Key="2"/><TRACK Key="3"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        path = self.root / "clash-convert.xml"
        path.write_text(xml, encoding="utf-8")

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
                    str(path),
                    "--playlist",
                    "ClashConvert",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertTrue((self.wav_dir / "Same" / "Hits" / "Intro.wav").is_file())
        second = self.wav_dir / "Same" / "Hits" / "intro (2).wav"
        second_alt = self.wav_dir / "Same" / "Hits" / "Intro (2).wav"
        self.assertTrue(second.is_file() or second_alt.is_file())
        self.assertTrue(
            (self.wav_dir / "Other" / "Solo" / "Clean Track.wav").is_file()
        )
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert_plan.source_key(intro)]["wav"]["dest"],
            "Same/Hits/Intro.wav",
        )
        second_dest = data["tracks"][convert_plan.source_key(intro2)]["wav"]["dest"]
        self.assertEqual(
            rb.collision_key(second_dest),
            rb.collision_key("Same/Hits/Intro (2).wav"),
        )
        self.assertIn(" (2).wav", second_dest)

    def test_convert_saves_manifest_with_preferred_dests_before_encode(
        self,
    ) -> None:
        """Given a fresh wav_dir: When main converts: Then
        rekordbox-converter-manifest.json exists before the first encode and
        records preferred Artist/Album/Name dests per source_key."""
        manifest_file = self.wav_dir / "rekordbox-converter-manifest.json"
        seen_before_encode: list[dict] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            self.assertTrue(
                manifest_file.is_file(), "manifest must exist before encode"
            )
            seen_before_encode.append(
                json.loads(manifest_file.read_text(encoding="utf-8"))
            )
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
        self.assertTrue(seen_before_encode)
        data = seen_before_encode[0]
        self.assertEqual(data["version"], 1)
        tracks = data["tracks"]
        bestial_key = convert_plan.source_key(self.a)
        self.assertEqual(
            tracks[bestial_key]["wav"]["dest"],
            "ABSL/It's just a bad dream/Bestial.wav",
        )
        self.assertEqual(
            tracks[convert_plan.source_key(self.b)]["wav"]["dest"],
            "Shogan/Hits/Revelation.wav",
        )
        self.assertEqual(
            tracks[convert_plan.source_key(self.c)]["wav"]["dest"],
            "Quantum/Hits/Movement.wav",
        )

    def test_sticky_manifest_reuses_dest_when_metadata_changes(self) -> None:
        """Given an existing wav assignment: When Artist/Album/Name change and
        convert reruns: Then the sticky dest path is reused (not Preferred) and
        import XML metadata is refreshed at that Location."""
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            rc1 = rb.main(
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
        self.assertEqual(rc1, 0)
        sticky = "ABSL/It's just a bad dream/Bestial.wav"
        sticky_path = self.wav_dir / Path(sticky)
        self.assertTrue(sticky_path.is_file())
        root = rb.load_dj_playlists(self.xml_path)
        track = root.find("COLLECTION/TRACK")
        assert track is not None
        track.set("Artist", "New Artist")
        track.set("Album", "New Album")
        track.set("Name", "New Name")
        ET.ElementTree(root).write(self.xml_path, encoding="UTF-8", xml_declaration=True)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=True
        ):
            rc2 = rb.main(
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
        self.assertEqual(rc2, 0)
        self.assertTrue(sticky_path.is_file())
        self.assertFalse(
            (self.wav_dir / "New Artist" / "New Album" / "New Name.wav").exists()
        )
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert_plan.source_key(self.a)]["wav"]["dest"],
            sticky,
        )
        out = ET.parse(self.output).getroot()
        sticky_loc = rb.encode_location(sticky_path)
        refreshed = next(
            t for t in out.findall("COLLECTION/TRACK") if t.get("Location") == sticky_loc
        )
        self.assertEqual(refreshed.get("Name"), "New Name")
        self.assertEqual(refreshed.get("Artist"), "New Artist")
        self.assertEqual(refreshed.get("Album"), "New Album")

    def test_sticky_aiff_metadata_change_refreshes_id3_without_rename(
        self,
    ) -> None:
        """Given a sticky AIFF dest: When Name/Artist/Album change and convert
        reruns: Then dest path is unchanged and ID3 text matches new metadata."""
        try:
            from test_cdj_safe_aiff import RATE_44100, write_pcm_aiff
        except ImportError:  # python -m unittest src.tests.…
            from src.tests.test_cdj_safe_aiff import RATE_44100, write_pcm_aiff

        sticky = "ABSL/It's just a bad dream/Bestial.aiff"
        aiff_writes: list[str] = []

        def fake_aiff(
            source: Path,
            dest: Path,
            source_el: ET.Element,
            **_kwargs,
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_pcm_aiff(dest, sample_rate_bytes=RATE_44100, bits=24)
            rb.write_aiff_id3(dest, source_el, None)
            aiff_writes.append(source_el.get("Name") or "")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "write_aiff_output", side_effect=fake_aiff):
            rc1 = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.root / "out-aiff.xml"),
                    "--format",
                    "aiff",
                ]
            )
        self.assertEqual(rc1, 0)
        sticky_path = self.wav_dir / Path(sticky)
        self.assertTrue(sticky_path.is_file())
        tag = rb._extract_id3_chunk(sticky_path)
        assert tag is not None
        text, _ = rb._read_id3_frames(tag)
        self.assertEqual(text.get("TIT2"), "Bestial")

        root = rb.load_dj_playlists(self.xml_path)
        track = root.find("COLLECTION/TRACK")
        assert track is not None
        track.set("Artist", "New Artist")
        track.set("Album", "New Album")
        track.set("Name", "New Name")
        ET.ElementTree(root).write(self.xml_path, encoding="UTF-8", xml_declaration=True)

        aiff_writes.clear()
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "write_aiff_output", side_effect=fake_aiff):
            rc2 = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.root / "out-aiff.xml"),
                    "--format",
                    "aiff",
                ]
            )
        self.assertEqual(rc2, 0)
        self.assertTrue(sticky_path.is_file())
        self.assertFalse(
            (self.wav_dir / "New Artist" / "New Album" / "New Name.aiff").exists()
        )
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert_plan.source_key(self.a)]["aiff"]["dest"],
            sticky,
        )
        self.assertIn("New Name", aiff_writes)
        tag2 = rb._extract_id3_chunk(sticky_path)
        assert tag2 is not None
        text2, _ = rb._read_id3_frames(tag2)
        self.assertEqual(text2.get("TIT2"), "New Name")
        self.assertEqual(text2.get("TPE1"), "New Artist")
        self.assertEqual(text2.get("TALB"), "New Album")

    def test_invalid_manifest_fails_before_audio_or_xml(self) -> None:
        """Given a corrupt manifest on disk: When main converts: Then exit is
        nonzero, stderr mentions the manifest, and no audio/XML is written."""
        self.wav_dir.mkdir(parents=True)
        (self.wav_dir / "rekordbox-converter-manifest.json").write_text(
            "{bad", encoding="utf-8"
        )
        stderr = io.StringIO()
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(sys, "stderr", stderr):
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
        self.assertIn("manifest", stderr.getvalue().casefold())
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.wav_dir.rglob("*.wav")))

    def test_failed_encodes_keep_manifest_reservations(self) -> None:
        """Given encode failures: When main returns nonzero: Then the manifest
        still retains the reserved dests from before conversion."""
        def boom(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            raise rb.CliError(f"boom {source.name}")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=boom), patch.object(
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
        self.assertEqual(rc, 1)
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert_plan.source_key(self.a)]["wav"]["dest"],
            "ABSL/It's just a bad dream/Bestial.wav",
        )

    def test_missing_dest_recreates_at_same_sticky_path(self) -> None:
        """Given a reserved sticky dest whose file was deleted: When convert
        reruns: Then the file is recreated at the same path (no Name (2))."""
        sticky = "ABSL/It's just a bad dream/Bestial.wav"
        wrote: list[Path] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")
            wrote.append(dest)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            rc1 = rb.main(
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
        self.assertEqual(rc1, 0)
        dest = self.wav_dir / Path(sticky)
        self.assertTrue(dest.is_file())
        dest.unlink()
        self.assertFalse(dest.exists())
        wrote.clear()

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            rc2 = rb.main(
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
        self.assertEqual(rc2, 0)
        self.assertTrue(dest.is_file())
        self.assertIn(dest, wrote)
        self.assertFalse(
            (self.wav_dir / "ABSL" / "It's just a bad dream" / "Bestial (2).wav").exists()
        )
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert_plan.source_key(self.a)]["wav"]["dest"],
            sticky,
        )

    def test_ceiling_raise_skips_when_effective_quality_unchanged(self) -> None:
        """Given a 16/44.1 source already converted: When the ceiling rises to
        24/48: Then convert skips (effective quality unchanged) at the sticky path."""
        sticky = "ABSL/It's just a bad dream/Bestial.wav"
        converted: list[Path] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_pcm_wav(dest, bits=16, sample_rate=44100)
            converted.append(dest)

        def probe16(path: Path) -> dict:
            if path.suffix.lower() == ".wav":
                return wav_probe(bits=16)
            return flac_probe(bits=16)

        args_low = [
            "--xml",
            str(self.xml_path),
            "--playlist",
            "Untitled Intelligent List",
            "--wav-dir",
            str(self.wav_dir),
            "--output",
            str(self.output),
            "--bit-depth",
            "16",
            "--sample-rate",
            "44100",
        ]
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe16
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg):
            self.assertEqual(rb.main(args_low), 0)
        dest = self.wav_dir / Path(sticky)
        self.assertTrue(dest.is_file())
        prior = dest.read_bytes()
        converted.clear()

        args_high = [
            "--xml",
            str(self.xml_path),
            "--playlist",
            "Untitled Intelligent List",
            "--wav-dir",
            str(self.wav_dir),
            "--output",
            str(self.output),
            "--bit-depth",
            "24",
            "--sample-rate",
            "48000",
        ]
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe16
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg):
            self.assertEqual(rb.main(args_high), 0)
        self.assertEqual(converted, [])
        self.assertEqual(dest.read_bytes(), prior)
        self.assertFalse(
            (self.wav_dir / "ABSL" / "It's just a bad dream" / "Bestial (2).wav").exists()
        )

    def test_effective_quality_mismatch_rebuilds_in_place_sticky_path(self) -> None:
        """Given a reduced dest for a higher-quality source: When the ceiling
        rises so effective depth/rate no longer matches: Then replace in place
        at the sticky path (no Name (2))."""
        sticky = "ABSL/It's just a bad dream/Bestial.wav"
        converted: list[Path] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            bits = kwargs.get("bit_depth", 16)
            rate = kwargs.get("sample_rate", 44100)
            write_pcm_wav(dest, bits=bits, sample_rate=rate)
            converted.append(dest)

        def probe24(path: Path) -> dict:
            if path.suffix.lower() == ".wav":
                return wav_probe(bits=24)
            return flac_probe(bits=24)

        args_low = [
            "--xml",
            str(self.xml_path),
            "--playlist",
            "Untitled Intelligent List",
            "--wav-dir",
            str(self.wav_dir),
            "--output",
            str(self.output),
            "--bit-depth",
            "16",
            "--sample-rate",
            "44100",
        ]
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe24
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg):
            self.assertEqual(rb.main(args_low), 0)
        dest = self.wav_dir / Path(sticky)
        self.assertTrue(
            convert_plan.is_cdj_safe_wav(dest, bit_depth=16, sample_rate=44100)
        )
        converted.clear()

        args_high = [
            "--xml",
            str(self.xml_path),
            "--playlist",
            "Untitled Intelligent List",
            "--wav-dir",
            str(self.wav_dir),
            "--output",
            str(self.output),
            "--bit-depth",
            "24",
            "--sample-rate",
            "48000",
        ]
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe24
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg):
            self.assertEqual(rb.main(args_high), 0)
        self.assertIn(dest, converted)
        self.assertTrue(
            convert_plan.is_cdj_safe_wav(dest, bit_depth=24, sample_rate=44100)
        )
        self.assertFalse(
            (self.wav_dir / "ABSL" / "It's just a bad dream" / "Bestial (2).wav").exists()
        )
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            data["tracks"][convert_plan.source_key(self.a)]["wav"]["dest"],
            sticky,
        )

    def test_wav_and_aiff_assignments_coexist_in_manifest(self) -> None:
        """Given the same source converted as wav then aiff: When manifests are
        saved: Then both format dests coexist under one source_key."""
        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        def fake_aiff(
            source: Path,
            dest: Path,
            source_el,
            **_kwargs,
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"FORM")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ), patch.object(convert_plan, "write_aiff_output", side_effect=fake_aiff):
            rc_wav = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.output),
                    "--format",
                    "wav",
                ]
            )
            rc_aiff = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                    "--output",
                    str(self.root / "out-aiff.xml"),
                    "--format",
                    "aiff",
                ]
            )
        self.assertEqual(rc_wav, 0)
        self.assertEqual(rc_aiff, 0)
        data = json.loads(
            (self.wav_dir / "rekordbox-converter-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        entry = data["tracks"][convert_plan.source_key(self.a)]
        self.assertEqual(
            entry["wav"]["dest"],
            "ABSL/It's just a bad dream/Bestial.wav",
        )
        self.assertEqual(
            entry["aiff"]["dest"],
            "ABSL/It's just a bad dream/Bestial.aiff",
        )

    def test_convert_refuses_dest_outside_wav_dir_before_write(self) -> None:
        """Given a planned dest that no longer resolves under wav_dir: When
        convert_unique runs: Then the track errors and no encode is attempted."""
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            plan, errors = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
            )
        self.assertEqual(errors, [])
        assert plan is not None
        item = plan.unique[0]
        encoded: list[Path] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            encoded.append(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(
            converter_manifest,
            "ensure_dest_path_under_wav_dir",
            side_effect=converter_manifest.ManifestError("escaped"),
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            stats = rb.convert_unique(plan, force=False, progress=False, items=[item])
        self.assertEqual(encoded, [])
        self.assertTrue(any("escaped" in e for e in stats.errors))

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
        self.assertIn("/WAV/ABSL/It%27s%20just%20a%20bad%20dream/", loc)
        self.assertTrue(
            (self.wav_dir / "ABSL" / "It's just a bad dream" / "Bestial.wav").is_file()
        )
        extra = first.find("EXTRA")
        self.assertIsNotNone(extra)
        assert extra is not None
        self.assertEqual(extra.get("Foo"), "bar")
        self.assertIsNotNone(first.find("TEMPO"))
        self.assertIsNotNone(first.find("POSITION_MARK"))
        pl = rb.find_playlists_by_name(out, "Untitled Intelligent List [WAV]")
        self.assertEqual(len(pl), 1)
        self.assertEqual([t.get("Key") for t in pl[0].findall("TRACK")], ["1", "2", "3"])

    def test_prepare_with_source_root_skips_load_dj_playlists(self) -> None:
        """Given a preloaded source_root: When prepare(..., source_root=): Then
        load_dj_playlists is not called for the source XML."""
        source_root = rb.load_dj_playlists(self.xml_path)
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(rb, "load_dj_playlists") as load_spy:
            plan, errors = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
                source_root=source_root,
            )
        self.assertEqual(errors, [])
        assert plan is not None
        load_spy.assert_not_called()

    def test_prepare_all_then_apply_keeps_every_playlist(self) -> None:
        """Batch-prepare playlists; convert unique (source, format) once; share tree."""
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

        encoded: list[str] = []

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ):
            manifest = converter_manifest.empty_manifest()
            plan_a, errors_a = rb.prepare(
                self.xml_path,
                "Untitled Intelligent List",
                self.wav_dir,
                self.output,
                manifest=manifest,
            )
            plan_b, errors_b = rb.prepare(
                self.xml_path,
                "Morning",
                self.wav_dir,
                self.output,
                manifest=manifest,
            )
            self.assertEqual(errors_a, [])
            self.assertEqual(errors_b, [])
            assert plan_a is not None and plan_b is not None
            self.assertIsNot(plan_a.output_root, plan_b.output_root)
            plans = [plan_a, plan_b]
            rb.share_output_root(plans)
            self.assertIs(plan_a.output_root, plan_b.output_root)
            items = rb.collect_batch_unique(plans)
            self.assertEqual(len(items), 3)
            stats = rb.convert_unique(plan_a, force=False, progress=False, items=items)
            for plan in plans:
                rb.apply_xml(plan, stats.succeeded)
            rb.write_import_xml(plan_a.output_root, plan_a.output)

        self.assertEqual(len(encoded), 3)
        self.assertEqual(encoded.count("07 - Bestial.flac"), 1)
        out = ET.parse(self.output).getroot()
        names = sorted(name for _folder, name, _node in rb.iter_playlists(out))
        self.assertEqual(names, ["Morning [WAV]", "Untitled Intelligent List [WAV]"])
        root_node = out.find("PLAYLISTS/NODE")
        assert root_node is not None
        self.assertEqual(root_node.get("Count"), "2")
        # Same Artist/Album/Name dest for Bestial in both playlists → one collection row.
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)
        morning_pl = rb.find_playlists_by_name(out, "Morning [WAV]")[0]
        self.assertEqual(len(morning_pl.findall("TRACK")), 1)
        bestial = [t for t in out.findall("COLLECTION/TRACK") if t.get("Name") == "Bestial"]
        self.assertEqual(len(bestial), 1)
        tid = bestial[0].get("TrackID")
        for pl_name in ("Untitled Intelligent List [WAV]", "Morning [WAV]"):
            pl = rb.find_playlists_by_name(out, pl_name)[0]
            self.assertIn(tid, [t.get("Key") for t in pl.findall("TRACK")])

    def test_cli_multi_playlist_encodes_shared_source_once(self) -> None:
        """Given Bestial in two WAV playlists: When CLI converts both in one run:
        Then ffmpeg encodes each source once; both playlists share one Bestial
        TrackID and one dest file."""
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

        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        wizard = (
            self.xml_path,
            [(None, "Untitled Intelligent List"), (None, "Morning")],
            self.wav_dir,
            self.output,
            "wav",
            24,
            48000,
        )
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            convert_plan, "is_cdj_safe_wav", return_value=False
        ), patch.object(rb.sys.stdin, "isatty", return_value=True), patch.object(
            rb, "prompt_wizard", return_value=wizard
        ):
            self.assertEqual(rb.main([]), 0)

        self.assertEqual(len(encoded), 3)
        self.assertEqual(encoded.count("07 - Bestial.flac"), 1)
        dest = self.wav_dir / "ABSL" / "It's just a bad dream" / "Bestial.wav"
        self.assertTrue(dest.is_file())
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)
        bestial = [t for t in out.findall("COLLECTION/TRACK") if t.get("Name") == "Bestial"]
        self.assertEqual(len(bestial), 1)
        tid = bestial[0].get("TrackID")
        for pl_name in ("Untitled Intelligent List [WAV]", "Morning [WAV]"):
            pl = rb.find_playlists_by_name(out, pl_name)[0]
            keys = [t.get("Key") for t in pl.findall("TRACK")]
            self.assertIn(tid, keys)

    def test_batch_convert_writes_import_xml_once(self) -> None:
        """Given two playlists in one CLI run: When conversion finishes: Then
        atomic_write_xml runs exactly once with both playlists present."""
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

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        write_calls: list[Path] = []
        real_write = xml_output.atomic_write_xml

        def spy_write(root: ET.Element, path: Path) -> None:
            write_calls.append(path)
            real_write(root, path)

        wizard = (
            self.xml_path,
            [(None, "Untitled Intelligent List"), (None, "Morning")],
            self.wav_dir,
            self.output,
            "wav",
            24,
            48000,
        )
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            convert_plan, "is_cdj_safe_wav", return_value=False
        ), patch.object(rb.sys.stdin, "isatty", return_value=True), patch.object(
            rb, "prompt_wizard", return_value=wizard
        ), patch.object(xml_output, "atomic_write_xml", side_effect=spy_write):
            self.assertEqual(rb.main([]), 0)

        self.assertEqual(len(write_calls), 1, "batch must write import XML once")
        out = ET.parse(self.output).getroot()
        names = sorted(name for _folder, name, _node in rb.iter_playlists(out))
        self.assertEqual(names, ["Morning [WAV]", "Untitled Intelligent List [WAV]"])

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
        self.assertFalse(
            (self.root / "WAV" / "rekordbox-converter-manifest.json").exists()
        )

    def test_main_omitted_output_writes_import_xml_under_wav_dir(self) -> None:
        """Given no --output: When main converts: Then import XML is
        <wav_dir>/rekordbox-import.xml."""

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert_plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            rb, "is_cdj_safe_wav", return_value=False
        ), patch("sys.stdout", io.StringIO()):
            rc = rb.main(
                [
                    "--xml",
                    str(self.xml_path),
                    "--playlist",
                    "Untitled Intelligent List",
                    "--wav-dir",
                    str(self.wav_dir),
                ]
            )
        self.assertEqual(rc, 0)
        derived = self.wav_dir / "rekordbox-import.xml"
        self.assertTrue(derived.is_file())
        self.assertFalse(self.output.exists())

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

    def test_parse_args_omitted_output_defaults_to_none(self) -> None:
        """Given no --output: When parse_args runs: Then output is None."""
        args = rb.parse_args(
            ["--xml", "in.xml", "--playlist", "P", "--wav-dir", "/tmp/lib"]
        )
        self.assertIsNone(args.output)

    def test_parse_args_explicit_output_is_kept(self) -> None:
        """Given explicit --output: When parse_args runs: Then that path is kept."""
        args = rb.parse_args(
            [
                "--xml",
                "in.xml",
                "--playlist",
                "P",
                "--wav-dir",
                "/tmp/lib",
                "--output",
                "/tmp/custom-import.xml",
            ]
        )
        self.assertEqual(args.output, Path("/tmp/custom-import.xml"))

    def test_resolve_cli_output_derives_from_wav_dir_when_omitted(self) -> None:
        """Given output=None: When resolve_cli_output runs: Then
        <wav_dir>/rekordbox-import.xml."""
        wav_dir = Path("/tmp/my-lib")
        self.assertEqual(
            rb.resolve_cli_output(wav_dir, None),
            wav_dir / "rekordbox-import.xml",
        )

    def test_resolve_cli_output_keeps_explicit_override(self) -> None:
        wav_dir = Path("/tmp/my-lib")
        explicit = Path("/tmp/elsewhere/import.xml")
        self.assertEqual(rb.resolve_cli_output(wav_dir, explicit), explicit)

    def test_prompt_paths_skips_xml_when_output_not_overridden(self) -> None:
        """Given no explicit --output: When prompt_paths runs: Then only the
        audio directory is prompted and XML is derived."""
        answers = iter(["/tmp/chosen-wav"])
        with patch("builtins.input", side_effect=lambda _p: next(answers)):
            wav, out = rb.prompt_paths(Path("/tmp/default-wav"), None)
        self.assertEqual(wav, Path("/tmp/chosen-wav"))
        self.assertEqual(out, Path("/tmp/chosen-wav") / "rekordbox-import.xml")

    def test_prompt_paths_asks_xml_when_output_overridden(self) -> None:
        answers = iter(["/tmp/chosen-wav", "/tmp/custom.xml"])
        with patch("builtins.input", side_effect=lambda _p: next(answers)):
            wav, out = rb.prompt_paths(
                Path("/tmp/default-wav"), Path("/tmp/override.xml")
            )
        self.assertEqual(wav, Path("/tmp/chosen-wav"))
        self.assertEqual(out, Path("/tmp/custom.xml"))


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

    def test_run_ffmpeg_timeout_scales_with_convert_workers(self) -> None:
        """Given CONVERT_WORKERS=4 and FFMPEG_CONVERT_TIMEOUT_S=100: When
        ffmpeg hangs past the per-worker budget: Then the CliError deadline is
        400s (timeout * workers), not the unscaled 100s."""
        communicated: list[bool] = []

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
                communicated.append(True)
                return "", ""

        src = Path("/tmp/src.flac")
        dest = Path("/tmp/out.wav")
        with patch.object(
            ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
        ), patch.object(
            ffmpeg_tools, "ffmpeg_supports_soxr", return_value=False
        ), patch.object(
            ffmpeg_tools, "FFMPEG_CONVERT_TIMEOUT_S", 100
        ), patch.object(
            convert_plan, "CONVERT_WORKERS", 4
        ), patch.object(convert_plan.subprocess, "Popen", FakeProc), patch.object(
            convert_plan.time, "sleep", lambda _s: None
        ), patch.object(
            convert_plan.time, "monotonic", side_effect=[0.0, 401.0]
        ):
            with self.assertRaises(rb.CliError) as ctx:
                convert_plan.run_ffmpeg(src, dest, "pcm_s16le", force=True)
        msg = str(ctx.exception).lower()
        self.assertIn("timed out", msg)
        self.assertIn("400", msg, f"deadline must scale to 100*4=400s; got {msg!r}")
        self.assertNotIn(
            "after 100s",
            msg,
            "must not report the unscaled per-process timeout alone",
        )
        self.assertTrue(communicated)

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
