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
import converter_manifest
import ffmpeg_tools
from convert_fixtures import write_flac, write_pcm_wav
from test_cdj_safe_aiff import write_pcm_aiff


class ConversionPreviewTests(unittest.TestCase):
    def _plan_with(
        self,
        item: rb.PlannedTrack,
        *,
        output_format: str = "wav",
        cover_cache: dict | None = None,
    ) -> rb.Plan:
        return rb.Plan(
            playlist_name="P",
            wav_playlist_name="P [WAV]" if output_format == "wav" else "P [AIFF]",
            wav_dir=item.dest_path.parent.parent
            if item.dest_path.parent.name in ("WAV", "AIFF")
            else item.dest_path.parent,
            playlist_dir=item.dest_path.parent,
            output=item.dest_path.parent / "o.xml",
            tracks=[item],
            unique=[item],
            source_root=ET.Element("DJ_PLAYLISTS"),
            output_root=ET.Element("DJ_PLAYLISTS"),
            output_existed=False,
            output_format=output_format,
            cover_cache=cover_cache if cover_cache is not None else {},
        )

    def test_planned_action_classifies_noop_reuse_copy_transcode_and_force(
        self,
    ) -> None:
        """Given noop/canonical/copy_wav/transcode items: When planned_action
        runs with and without force: Then actions follow the shared rules."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            src = root / "src.flac"
            src.write_bytes(b"fLaC")

            # noop → reuse even under force
            noop_dest = root / "WAV" / "noop.wav"
            noop_dest.parent.mkdir(parents=True)
            noop = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=noop_dest,
                dest_location=rb.encode_location(noop_dest),
                dest_name=noop_dest.name,
                codec=None,
                copy_wav=True,
                noop=True,
                bit_depth=16,
                sample_rate=44100,
            )
            plan = self._plan_with(noop)
            self.assertEqual(rb.planned_action(plan, noop, force=False), "reuse")
            self.assertEqual(rb.planned_action(plan, noop, force=True), "reuse")

            # canonical WAV dest + not force → reuse; force → rebuild
            wav_dest = root / "WAV" / "safe.wav"
            write_pcm_wav(wav_dest, bits=16, sample_rate=44100)
            wav_item = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=wav_dest,
                dest_location=rb.encode_location(wav_dest),
                dest_name=wav_dest.name,
                codec="pcm_s16le",
                copy_wav=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
            )
            plan = self._plan_with(wav_item)
            self.assertEqual(rb.planned_action(plan, wav_item, force=False), "reuse")
            self.assertEqual(
                rb.planned_action(plan, wav_item, force=True), "transcode"
            )

            # canonical AIFF dest + not force → reuse
            aiff_src = root / "safe.aiff"
            write_pcm_aiff(aiff_src)
            aiff_dest = root / "AIFF" / "out.aiff"
            aiff_dest.parent.mkdir(parents=True)
            __import__("shutil").copy2(aiff_src, aiff_dest)
            rb.write_aiff_id3(aiff_dest, el, None)
            aiff_item = rb.PlannedTrack(
                source_el=el,
                source_path=aiff_src,
                dest_path=aiff_dest,
                dest_location=rb.encode_location(aiff_dest),
                dest_name=aiff_dest.name,
                codec=None,
                copy_wav=True,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
            )
            plan = self._plan_with(aiff_item, output_format="aiff")
            self.assertEqual(
                rb.planned_action(plan, aiff_item, force=False), "reuse"
            )

            # copy_wav → copy (no existing dest)
            copy_dest = root / "WAV" / "copy.wav"
            copy_item = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=copy_dest,
                dest_location=rb.encode_location(copy_dest),
                dest_name=copy_dest.name,
                codec=None,
                copy_wav=True,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
            )
            plan = self._plan_with(copy_item)
            self.assertEqual(rb.planned_action(plan, copy_item, force=False), "copy")

            # else → transcode
            tx_dest = root / "WAV" / "tx.wav"
            tx_item = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=tx_dest,
                dest_location=rb.encode_location(tx_dest),
                dest_name=tx_dest.name,
                codec="pcm_s24le",
                copy_wav=False,
                noop=False,
                bit_depth=24,
                sample_rate=48000,
            )
            plan = self._plan_with(tx_item)
            self.assertEqual(
                rb.planned_action(plan, tx_item, force=False), "transcode"
            )

    def test_planned_action_aiff_missing_dest_skips_cover_extract(self) -> None:
        """Given AIFF item with missing dest: When planned_action runs:
        Then extract_cover_jpeg is not called and action is copy/transcode."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            src = root / "src.flac"
            src.write_bytes(b"fLaC")
            dest = root / "AIFF" / "out.aiff"
            dest.parent.mkdir(parents=True)
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
            plan = self._plan_with(item, output_format="aiff")
            with patch.object(
                convert_plan, "extract_cover_jpeg", return_value=None
            ) as extract:
                action = rb.planned_action(plan, item, force=False)
            self.assertEqual(action, "copy")
            extract.assert_not_called()

            tx_item = rb.PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=rb.encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s24le",
                copy_wav=False,
                noop=False,
                bit_depth=24,
                sample_rate=48000,
            )
            plan = self._plan_with(tx_item, output_format="aiff")
            with patch.object(
                convert_plan, "extract_cover_jpeg", return_value=None
            ) as extract:
                action = rb.planned_action(plan, tx_item, force=False)
            self.assertEqual(action, "transcode")
            extract.assert_not_called()

    def test_build_conversion_preview_stops_when_cancel_event_set(self) -> None:
        """Given cancel_event set after the first item: When
        build_conversion_preview runs: Then CancelledError is raised and later
        items are not classified."""
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            items: list[rb.PlannedTrack] = []
            for i in range(3):
                src = root / f"src{i}.flac"
                src.write_bytes(b"fLaC")
                dest = root / "AIFF" / f"out{i}.aiff"
                dest.parent.mkdir(parents=True, exist_ok=True)
                items.append(
                    rb.PlannedTrack(
                        source_el=el,
                        source_path=src,
                        dest_path=dest,
                        dest_location=rb.encode_location(dest),
                        dest_name=dest.name,
                        codec="pcm_s24le",
                        copy_wav=False,
                        noop=False,
                        bit_depth=24,
                        sample_rate=48000,
                    )
                )
            plan = rb.Plan(
                playlist_name="P",
                wav_playlist_name="P [AIFF]",
                wav_dir=root,
                playlist_dir=root / "AIFF",
                output=root / "o.xml",
                tracks=items,
                unique=items,
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
                output_format="aiff",
            )
            cancel = threading.Event()
            calls: list[str] = []

            def action_side_effect(plan_arg, item, force, **_kwargs):
                calls.append(item.dest_name)
                if len(calls) == 1:
                    cancel.set()
                return "transcode"

            with patch.object(
                convert_plan, "planned_action", side_effect=action_side_effect
            ), patch.object(convert_plan, "CONVERT_WORKERS", 1):
                with self.assertRaises(rb.CancelledError):
                    rb.build_conversion_preview(
                        [plan], items, force=False, cancel_event=cancel
                    )
            self.assertEqual(calls, ["out0.aiff"])

    def test_build_conversion_preview_classifies_concurrently(self) -> None:
        """Given CONVERT_WORKERS>1: When build_conversion_preview runs:
        Then at least two planned_action calls overlap."""
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            items: list[rb.PlannedTrack] = []
            for i in range(3):
                src = root / f"src{i}.flac"
                src.write_bytes(b"fLaC")
                dest = root / "WAV" / f"out{i}.wav"
                dest.parent.mkdir(parents=True, exist_ok=True)
                items.append(
                    rb.PlannedTrack(
                        source_el=el,
                        source_path=src,
                        dest_path=dest,
                        dest_location=rb.encode_location(dest),
                        dest_name=dest.name,
                        codec="pcm_s24le",
                        copy_wav=False,
                        noop=False,
                        bit_depth=24,
                        sample_rate=48000,
                    )
                )
            plan = self._plan_with(items[0])
            plan.tracks = items
            plan.unique = items

            barrier = threading.Barrier(2)
            lock = threading.Lock()
            barrier_slots = 0
            overlapped = threading.Event()

            def action_with_overlap(plan_arg, item, force, **_kwargs):
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
                return "transcode"

            with patch.object(
                convert_plan, "planned_action", side_effect=action_with_overlap
            ), patch.object(convert_plan, "CONVERT_WORKERS", 2):
                preview = rb.build_conversion_preview([plan], items, force=False)
            self.assertTrue(
                overlapped.is_set(),
                "expected at least two planned_action calls to overlap "
                "under CONVERT_WORKERS>1",
            )
            self.assertEqual(len(preview.items), 3)
            self.assertEqual(
                [p.relative_dest for p in preview.items],
                [item.dest_path.relative_to(root).as_posix() for item in items],
            )

    def test_build_conversion_preview_reports_progress(self) -> None:
        """Given unique items: When build_conversion_preview runs with
        on_progress: Then each item emits preview progress."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            items: list[rb.PlannedTrack] = []
            for i in range(2):
                src = root / f"src{i}.flac"
                src.write_bytes(b"fLaC")
                dest = root / "WAV" / f"out{i}.wav"
                dest.parent.mkdir(parents=True, exist_ok=True)
                items.append(
                    rb.PlannedTrack(
                        source_el=el,
                        source_path=src,
                        dest_path=dest,
                        dest_location=rb.encode_location(dest),
                        dest_name=dest.name,
                        codec="pcm_s24le",
                        copy_wav=False,
                        noop=False,
                        bit_depth=24,
                        sample_rate=48000,
                    )
                )
            plan = self._plan_with(items[0])
            plan.tracks = items
            plan.unique = items
            progress_calls: list[tuple[int, int, str, str]] = []

            def on_progress(current: int, total: int, action: str, name: str) -> None:
                progress_calls.append((current, total, action, name))

            with patch.object(convert_plan, "CONVERT_WORKERS", 1):
                preview = rb.build_conversion_preview(
                    [plan], items, force=False, on_progress=on_progress
                )
            self.assertEqual(len(preview.items), 2)
            self.assertEqual(
                progress_calls,
                [
                    (1, 2, "preview", "out0.wav"),
                    (2, 2, "preview", "out1.wav"),
                ],
            )

    def test_preview_counts_and_item_fields_use_effective_quality(self) -> None:
        """Given two playlists sharing one source plus a missing track and a
        collision suffix: When build_conversion_preview runs: Then counts and
        per-item fields use relative dest, action, and effective quality."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            music = root / "music"
            a = music / "A.flac"
            b = music / "B.flac"
            for p in (a, b):
                write_flac(p)
            missing = music / "Missing.flac"
            xml_path = root / "c.xml"
            xml_path.write_text(
                f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="1" Name="Song" Artist="Same"
           Location="{rb.encode_location(a)}" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="Song" Artist="Same"
           Location="{rb.encode_location(b)}" Kind="FLAC File"/>
    <TRACK TrackID="3" Name="Gone" Artist="X"
           Location="{rb.encode_location(missing)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="P1" Type="1" KeyType="0" Entries="3">
        <TRACK Key="1"/><TRACK Key="2"/><TRACK Key="3"/>
      </NODE>
      <NODE Name="P2" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
""",
                encoding="utf-8",
            )
            wav_dir = root / "out"
            output = root / "import.xml"

            def probe(path: Path, **_kwargs: object) -> dict:
                return {
                    "format": {"format_name": "flac", "duration": "10.0"},
                    "streams": [
                        {
                            "codec_name": "flac",
                            "sample_fmt": "s16",
                            "sample_rate": "44100",
                            "channels": 2,
                            "bits_per_raw_sample": "16",
                        }
                    ],
                }

            with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
                ffmpeg_tools, "run_ffprobe", side_effect=probe
            ):
                manifest = converter_manifest.empty_manifest()
                plan1, err1 = rb.prepare(
                    xml_path,
                    "P1",
                    wav_dir,
                    output,
                    max_bit_depth=24,
                    max_sample_rate=48000,
                    manifest=manifest,
                )
                plan2, err2 = rb.prepare(
                    xml_path,
                    "P2",
                    wav_dir,
                    output,
                    max_bit_depth=24,
                    max_sample_rate=48000,
                    manifest=manifest,
                )
            self.assertEqual(err1, [])
            self.assertEqual(err2, [])
            assert plan1 is not None and plan2 is not None
            plans = [plan1, plan2]
            rb.share_output_root(plans)
            items = rb.collect_batch_unique(plans)
            preview = rb.build_conversion_preview(plans, items, force=False)

            # P1: 2 resolved + 1 missing; P2: 1 resolved (shared source)
            self.assertEqual(preview.resolved, 3)
            self.assertEqual(preview.missing, 1)
            self.assertEqual(preview.selected, 4)
            self.assertEqual(preview.unique_outputs, 2)
            self.assertEqual(preview.duplicates, 1)

            by_dest = {it.relative_dest: it for it in preview.items}
            self.assertIn("WAV/Same - Song.wav", by_dest)
            self.assertIn("WAV/Same - Song (2).wav", by_dest)
            for it in preview.items:
                self.assertEqual(it.action, "transcode")
                self.assertEqual(it.bit_depth, 16)
                self.assertEqual(it.sample_rate, 44100)
                self.assertTrue(it.size_display.startswith("≈ "))
                self.assertTrue(it.size_display.endswith(" MB"))

    def test_preview_write_bytes_sums_copy_and_transcode_ignores_reuse_and_unknown(
        self,
    ) -> None:
        """Given preview rows: When summing write bytes: Then only copy/transcode
        with known sizes count."""
        preview = rb.ConversionPreview(
            selected=4,
            resolved=4,
            unique_outputs=4,
            duplicates=0,
            missing=0,
            items=[
                rb.ConversionPreviewItem(
                    relative_dest="a.wav",
                    action="reuse",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=9_000_000,
                    size_display="8.6 MB",
                ),
                rb.ConversionPreviewItem(
                    relative_dest="b.wav",
                    action="copy",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=1000,
                    size_display="≈ 0.0 MB",
                ),
                rb.ConversionPreviewItem(
                    relative_dest="c.wav",
                    action="transcode",
                    bit_depth=24,
                    sample_rate=48000,
                    size_bytes=2500,
                    size_display="≈ 0.0 MB",
                ),
                rb.ConversionPreviewItem(
                    relative_dest="d.wav",
                    action="transcode",
                    bit_depth=24,
                    sample_rate=48000,
                    size_bytes=None,
                    size_display="—",
                ),
            ],
        )
        self.assertEqual(rb.preview_write_bytes(preview), 3500)

    def test_insufficient_output_space_message_when_free_below_required(
        self,
    ) -> None:
        """Given required bytes above free space: When checking: Then a clear
        message; enough free or probe failure: Then None."""
        from types import SimpleNamespace

        path = Path("/tmp")
        msg = rb.insufficient_output_space_message(
            path,
            5_000_000,
            disk_usage=lambda _p: SimpleNamespace(free=1_000_000),
        )
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("Not enough free space in the output folder", msg)
        self.assertIn("needed", msg)
        self.assertIn("available", msg)
        self.assertIn("MB", msg)

        self.assertIsNone(
            rb.insufficient_output_space_message(
                path,
                5_000_000,
                disk_usage=lambda _p: SimpleNamespace(free=10_000_000),
            )
        )
        self.assertIsNone(
            rb.insufficient_output_space_message(path, 0, disk_usage=lambda _p: None)
        )
        self.assertIsNone(
            rb.insufficient_output_space_message(
                path,
                5_000_000,
                disk_usage=lambda _p: (_ for _ in ()).throw(OSError("boom")),
            )
        )


if __name__ == "__main__":
    unittest.main()

