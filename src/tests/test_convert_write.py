#!/usr/bin/env python3
"""Write-port tests: execute_prepared owns save + convert + apply + write."""

from __future__ import annotations

import os
import sys
import tempfile
import unicodedata
import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_SRC, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import converter_manifest
import ffmpeg_tools
import cdj_wav
import convert.plan
import xml_output
from cli_error import CliError
from convert.freshness import (
    assignment_state,
    bind_complete_assignment,
    metadata_signature,
    output_signature,
    source_signature,
)
from convert.models import ConversionPreview, Plan, PlannedTrack, PreparedConversion
from convert.paths import collision_key, source_key
from convert.prepare import prepare_batch
from convert.rerun import classify_item
from convert.write import ManifestPersistError, convert_unique, execute_prepared
from convert_fixtures import XmlFixtureTests as XmlFixtureBase, write_pcm_wav
from rekordbox_xml import encode_location, iter_playlists, skeleton_from


class ExecutePreparedTests(XmlFixtureBase):
    def test_execute_prepared_saves_converts_applies_and_writes_import_xml(
        self,
    ) -> None:
        """Given a PreparedConversion: When execute_prepared runs: Then the
        manifest is saved, unique tracks convert once, each plan gets apply_xml,
        and import XML is written."""
        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            from convert.rerun import Decision

            self.assertTrue(prepared.decisions)
            self.assertTrue(
                all(isinstance(d, Decision) for d in prepared.decisions.values())
            )

            stats = execute_prepared(prepared, force=False, progress=False)

        self.assertEqual(stats.converted, 3)
        self.assertEqual(stats.recreated, 0)
        self.assertEqual(len(encoded), 3)
        self.assertTrue((self.wav_dir / converter_manifest.MANIFEST_NAME).is_file())
        for item in prepared.items:
            self.assertTrue(item.dest_path.is_file())
        self.assertTrue(self.output.is_file())
        out = ET.parse(self.output).getroot()
        names = [name for _folder, name, _node in iter_playlists(out)]
        self.assertEqual(names, ["Untitled Intelligent List [WAV]"])
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)

    def test_execute_prepared_reports_import_xml_progress_after_convert(
        self,
    ) -> None:
        """Given on_progress: When execute_prepared finishes audio jobs: Then
        it emits an import_xml progress tick before writing Import XML."""
        progress_actions: list[str] = []
        apply_seen = False

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        def on_progress(
            current: int, total: int, action: str, name: str
        ) -> None:
            progress_actions.append(action)
            if action == "import_xml":
                self.assertFalse(
                    apply_seen,
                    "import_xml progress must fire before apply_xml",
                )

        real_apply = xml_output.apply_xml

        def apply_and_mark(plan, success):
            nonlocal apply_seen
            apply_seen = True
            self.assertIn(
                "import_xml",
                progress_actions,
                "import_xml progress must fire before apply_xml",
            )
            return real_apply(plan, success)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ), patch.object(xml_output, "apply_xml", side_effect=apply_and_mark):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            execute_prepared(
                prepared, force=False, progress=False, on_progress=on_progress
            )

        self.assertIn("import_xml", progress_actions)
        self.assertTrue(apply_seen)
        convert_idxs = [
            i for i, a in enumerate(progress_actions) if a == "convert"
        ]
        import_idxs = [
            i for i, a in enumerate(progress_actions) if a == "import_xml"
        ]
        self.assertTrue(convert_idxs)
        self.assertTrue(import_idxs)
        self.assertGreater(import_idxs[0], convert_idxs[-1])

    def test_execute_prepared_aborts_when_manifest_replaced_after_prepare(
        self,
    ) -> None:
        """Given a prepared batch: When the manifest file is replaced before
        execute: Then convert aborts, newer assignments stay on disk, and
        Import XML is not written."""
        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            other = converter_manifest.empty_manifest()
            other.set_dest("/other/source.flac", "wav", "WAV/Other - Track.wav")
            converter_manifest.save_manifest(other, self.wav_dir)
            with self.assertRaises(CliError) as ctx:
                execute_prepared(prepared, force=False, progress=False)
        self.assertRegex(str(ctx.exception).casefold(), r"re-prepare|changed")
        self.assertEqual(encoded, [])
        self.assertFalse(self.output.exists())
        on_disk = converter_manifest.load_manifest(self.wav_dir)
        self.assertEqual(
            on_disk.get_dest("/other/source.flac", "wav"),
            "WAV/Other - Track.wav",
        )

    def test_prepare_batch_does_not_attach_disk_fingerprint_to_supplied_manifest(
        self,
    ) -> None:
        """Given a caller-supplied manifest and no fingerprint: When disk
        already has different tracks: Then prepare does not invent a
        fingerprint for that unrelated in-memory object."""
        on_disk = converter_manifest.empty_manifest()
        on_disk.set_dest("/new", "wav", "WAV/New.wav")
        converter_manifest.save_manifest(on_disk, self.wav_dir)
        supplied = converter_manifest.empty_manifest()

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
                manifest=supplied,
            )
        self.assertEqual(errors, [])
        assert prepared is not None
        self.assertIsNone(prepared.fingerprint)
        self.assertNotIn("/new", prepared.manifest.tracks)

    def test_execute_prepared_aborts_on_alternate_case_sibling_after_prepare(
        self,
    ) -> None:
        """Given a reserved dest: When an alternate-case sibling appears before
        execute: Then there is no encode, a conflict, and no Import XML."""
        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            dest = next(
                item.dest_path
                for item in prepared.items
                if "Bestial" in item.dest_path.name
            )
            dest.parent.mkdir(parents=True, exist_ok=True)
            sibling = dest.with_name("absl - bestial.wav")
            sibling.write_bytes(b"UNRELATED")
            if sibling.exists() and dest.exists() and sibling.samefile(dest):
                self.skipTest("filesystem is case-insensitive")
            with self.assertRaises(CliError) as ctx:
                execute_prepared(prepared, force=False, progress=False)
        self.assertRegex(str(ctx.exception).casefold(), r"conflict|collision")
        self.assertEqual(encoded, [])
        self.assertFalse(self.output.exists())
        self.assertFalse(dest.exists())
        self.assertEqual(sibling.read_bytes(), b"UNRELATED")

    def test_execute_prepared_aborts_when_refreshed_inventory_has_collision_sibling(
        self,
    ) -> None:
        """Given a reserved dest: When refresh sees another file with the same
        collision_key that is not the dest: Then execute aborts before encode."""
        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            assert prepared.reservation is not None
            dest = prepared.items[0].dest_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            extra = dest.parent / "injected-collision.wav"
            extra.write_bytes(b"UNRELATED")
            key = collision_key(dest.name)
            orig_refresh = prepared.reservation.refresh_inventory

            def refresh_then_inject() -> None:
                orig_refresh()
                prepared.reservation.inventory[key] = prepared.reservation.inventory.get(
                    key, ()
                ) + (extra,)

            prepared.reservation.refresh_inventory = refresh_then_inject
            with self.assertRaises(CliError) as ctx:
                execute_prepared(prepared, force=False, progress=False)
        self.assertRegex(str(ctx.exception).casefold(), r"conflict|collision")
        self.assertEqual(encoded, [])
        self.assertFalse(self.output.exists())
        self.assertFalse(dest.exists())
        self.assertEqual(extra.read_bytes(), b"UNRELATED")

    def test_execute_prepared_same_file_inventory_match_is_not_batch_conflict(
        self,
    ) -> None:
        """Given the planned dest already on disk: When inventory refresh sees
        only that file: Then execute does not abort as a dest collision."""
        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            dest = prepared.items[0].dest_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"EXISTING")
            stats = execute_prepared(prepared, force=False, progress=False)
        self.assertIsNotNone(stats)
        self.assertTrue(self.output.exists())

    def test_execute_prepared_nfc_plan_nfd_dirent_is_not_batch_conflict(self) -> None:
        """Given a planned NFC dest whose format dir lists the NFD spelling of
        the same file, and pathlib.samefile disagrees (APFS/ExFAT alias): When
        execute_prepared runs: Then it does not abort as a destination collision."""
        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        orig_samefile = Path.samefile

        def aliasing_samefile(self: Path, other: str | Path) -> bool:
            other_path = Path(other)
            if (
                unicodedata.normalize("NFC", self.name)
                == unicodedata.normalize("NFC", other_path.name)
                and self.name != other_path.name
            ):
                return False
            return orig_samefile(self, other)

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ), patch.object(Path, "samefile", aliasing_samefile):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            item = prepared.items[0]
            parent = item.dest_path.parent
            parent.mkdir(parents=True, exist_ok=True)
            stem = "Cauet la Pensée"
            nfc_name = unicodedata.normalize("NFC", stem) + item.dest_path.suffix
            nfd_name = unicodedata.normalize("NFD", stem) + item.dest_path.suffix
            if nfc_name == nfd_name:
                self.skipTest("Unicode NFC and NFD spellings are identical")
            nfc_dest = parent / nfc_name
            nfd_dest = parent / nfd_name
            nfd_dest.write_bytes(b"EXISTING")
            item.dest_path = nfc_dest
            item.dest_name = nfc_name
            orig_refresh = prepared.reservation.refresh_inventory

            def refresh_list_nfd() -> None:
                orig_refresh()
                key = collision_key(nfc_name)
                prepared.reservation.inventory[key] = (nfd_dest,)

            prepared.reservation.refresh_inventory = refresh_list_nfd
            stats = execute_prepared(prepared, force=False, progress=False)
        self.assertIsNotNone(stats)
        self.assertTrue(self.output.exists())

    def test_late_dest_change_after_pending_stays_complete_conflict(self) -> None:
        """Given a frozen transcode with pending incomplete: When dest changes
        before write: Then execute reports conflict, disk stays complete, and
        the next classify is conflict not rebuild."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            prior = dest.read_bytes()
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            manifest = converter_manifest.empty_manifest()
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=source_root,
                output_root=output_root,
                output_existed=False,
                manifest=manifest,
            )
            bind_complete_assignment(manifest, item, "WAV/A.wav")
            from convert.rerun import Decision

            src_stat = source_signature(src)
            out_stat = output_signature(dest)
            decision = Decision(
                action="transcode",
                reason="force",
                write_kind="audio",
                source_stat={"size": src_stat["size"], "mtime_ns": src_stat["mtime_ns"]},
                dest_stat={"size": out_stat["size"], "mtime_ns": out_stat["mtime_ns"]},
            )
            prepared = PreparedConversion(
                plans=[plan],
                items=[item],
                manifest=manifest,
                preview=ConversionPreview(
                    selected=1,
                    resolved=1,
                    unique_outputs=1,
                    duplicates=0,
                    missing=0,
                    items=[],
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
                decisions={(source_key(src), "wav"): decision},
            )
            st = dest.stat()
            os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
            encoded: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                encoded.append(dest_path)
                dest_path.write_bytes(b"OVERWRITTEN")

            with patch.object(
                convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("1", "1411", "44100")
            ):
                stats = execute_prepared(prepared, force=True, checkpoint_interval_s=0)
            self.assertEqual(
                stats.conflicts, [f"{src.name} → {dest.parent.name}/{dest.name}"]
            )
            self.assertEqual(encoded, [])
            self.assertEqual(dest.read_bytes(), prior)
            on_disk = converter_manifest.load_manifest(root)
            record = on_disk.tracks[source_key(src)]["wav"]
            self.assertEqual(assignment_state(record), "complete")
            plan.manifest = on_disk
            self.assertEqual(
                classify_item(plan, item, False).action,
                "external_modification_conflict",
            )

    def test_late_source_change_after_freeze_reports_state_changed(self) -> None:
        """Given a frozen transcode: When source changes before write: Then
        execute reports state_changed, does not overwrite dest, and disk stays
        complete."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            prior = dest.read_bytes()
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            manifest = converter_manifest.empty_manifest()
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=source_root,
                output_root=output_root,
                output_existed=False,
                manifest=manifest,
            )
            bind_complete_assignment(manifest, item, "WAV/A.wav")
            from convert.rerun import Decision

            src_stat = source_signature(src)
            out_stat = output_signature(dest)
            decision = Decision(
                action="transcode",
                reason="force",
                write_kind="audio",
                source_stat={"size": src_stat["size"], "mtime_ns": src_stat["mtime_ns"]},
                dest_stat={"size": out_stat["size"], "mtime_ns": out_stat["mtime_ns"]},
            )
            prepared = PreparedConversion(
                plans=[plan],
                items=[item],
                manifest=manifest,
                preview=ConversionPreview(
                    selected=1,
                    resolved=1,
                    unique_outputs=1,
                    duplicates=0,
                    missing=0,
                    items=[],
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
                decisions={(source_key(src), "wav"): decision},
            )
            src.write_bytes(b"fLaCchanged")
            encoded: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                encoded.append(dest_path)
                dest_path.write_bytes(b"OVERWRITTEN")

            with patch.object(
                convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("1", "1411", "44100")
            ):
                stats = execute_prepared(prepared, force=True)
            self.assertEqual(
                stats.state_changed, [f"{src.name} → {dest.parent.name}/{dest.name}"]
            )
            self.assertEqual(stats.conflicts, [])
            self.assertEqual(encoded, [])
            self.assertEqual(dest.read_bytes(), prior)
            on_disk = converter_manifest.load_manifest(root)
            record = on_disk.tracks[source_key(src)]["wav"]
            self.assertEqual(assignment_state(record), "complete")

    def test_convert_unique_reclassifies_mtime_change_as_conflict(self) -> None:
        """Given preview would reuse: When dest mtime changes before write:
        Then convert_unique reports a conflict and does not overwrite dest."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            prior = dest.read_bytes()
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=encode_location(dest),
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
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
            st = dest.stat()
            os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            encoded: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                encoded.append(dest_path)
                dest_path.write_bytes(b"OVERWRITTEN")

            with patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg):
                stats = convert_unique(plan, force=False)
            self.assertEqual(
                stats.conflicts, [f"{src.name} → {dest.parent.name}/{dest.name}"]
            )
            self.assertEqual(stats.converted, 0)
            self.assertEqual(encoded, [])
            self.assertEqual(dest.read_bytes(), prior)
            self.assertEqual(stats.succeeded, set())

    def test_refresh_xml_skips_ffmpeg_and_preserves_track_id(self) -> None:
        """Given a complete WAV dest and a metadata-only change: When execute:
        Then ffmpeg is not called, dest bytes stay, Import XML Name updates,
        and TrackID plus playlist Key are preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            prior = dest.read_bytes()
            el = ET.Element("TRACK", {"Name": "Old", "Artist": "DJ"})
            loc = encode_location(dest)
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=loc,
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            collection = output_root.find("COLLECTION")
            assert collection is not None
            ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": "99",
                    "Name": "Old",
                    "Artist": "DJ",
                    "Location": loc,
                    "Kind": "WAV File",
                },
            )
            playlists = output_root.find("PLAYLISTS")
            assert playlists is not None
            root_node = playlists.find("NODE")
            assert root_node is not None
            playlist = ET.SubElement(
                root_node,
                "NODE",
                {
                    "Name": "P [WAV]",
                    "Type": "1",
                    "KeyType": "0",
                    "Entries": "1",
                },
            )
            ET.SubElement(playlist, "TRACK", {"Key": "99"})
            manifest = converter_manifest.empty_manifest()
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=source_root,
                output_root=output_root,
                output_existed=True,
                manifest=manifest,
            )
            bind_complete_assignment(manifest, item, "WAV/A.wav")
            el.set("Name", "New")
            prepared = PreparedConversion(
                plans=[plan],
                items=[item],
                manifest=manifest,
                preview=ConversionPreview(
                    selected=1,
                    resolved=1,
                    unique_outputs=1,
                    duplicates=0,
                    missing=0,
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
            )
            encoded: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                encoded.append(dest_path)
                dest_path.write_bytes(b"OVERWRITTEN")

            with patch.object(
                convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("100", "1411", "44100")
            ):
                stats = execute_prepared(prepared, force=False)
            self.assertEqual(encoded, [])
            self.assertEqual(stats.converted, 0)
            self.assertEqual(stats.skipped, 1)
            self.assertEqual(dest.read_bytes(), prior)
            written = ET.parse(plan.output).getroot()
            tracks = written.findall("COLLECTION/TRACK")
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].get("TrackID"), "99")
            self.assertEqual(tracks[0].get("Name"), "New")
            keys = [
                node.get("Key")
                for node in written.findall("PLAYLISTS/NODE/NODE/TRACK")
            ]
            self.assertEqual(keys, ["99"])
            record = converter_manifest.load_manifest(root).tracks[
                source_key(src)
            ]["wav"]
            self.assertEqual(
                record["metadata"]["signature"],
                metadata_signature(el),
            )
            self.assertEqual(classify_item(plan, item, False).action, "reuse")

    def test_failed_import_xml_does_not_advance_refresh_xml_metadata(self) -> None:
        """Given refresh_xml: When Import XML writing fails: Then the stored
        metadata signature stays the previous one and the next classify is
        still refresh_xml."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            el = ET.Element("TRACK", {"Name": "Old", "Artist": "DJ"})
            loc = encode_location(dest)
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=loc,
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            collection = output_root.find("COLLECTION")
            assert collection is not None
            ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": "99",
                    "Name": "Old",
                    "Artist": "DJ",
                    "Location": loc,
                    "Kind": "WAV File",
                },
            )
            playlists = output_root.find("PLAYLISTS")
            assert playlists is not None
            root_node = playlists.find("NODE")
            assert root_node is not None
            playlist = ET.SubElement(
                root_node,
                "NODE",
                {
                    "Name": "P [WAV]",
                    "Type": "1",
                    "KeyType": "0",
                    "Entries": "1",
                },
            )
            ET.SubElement(playlist, "TRACK", {"Key": "99"})
            manifest = converter_manifest.empty_manifest()
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=source_root,
                output_root=output_root,
                output_existed=True,
                manifest=manifest,
            )
            bind_complete_assignment(manifest, item, "WAV/A.wav")
            previous_sig = manifest.tracks[source_key(src)]["wav"]["metadata"][
                "signature"
            ]
            el.set("Name", "New")
            prepared = PreparedConversion(
                plans=[plan],
                items=[item],
                manifest=manifest,
                preview=ConversionPreview(
                    selected=1,
                    resolved=1,
                    unique_outputs=1,
                    duplicates=0,
                    missing=0,
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
            )

            def fail_xml(_root, _path) -> None:
                raise CliError("import xml write failed")

            with patch.object(
                xml_output, "probe_dest_tech", return_value=("100", "1411", "44100")
            ), patch.object(xml_output, "write_import_xml", side_effect=fail_xml):
                with self.assertRaises(CliError):
                    execute_prepared(prepared, force=False)
            self.assertFalse(plan.output.exists())
            loaded = converter_manifest.load_manifest(root)
            record = loaded.tracks[source_key(src)]["wav"]
            self.assertEqual(record["metadata"]["signature"], previous_sig)
            self.assertNotEqual(previous_sig, metadata_signature(el))
            plan.manifest = loaded
            self.assertEqual(classify_item(plan, item, False).action, "refresh_xml")

    def test_post_xml_metadata_persist_failure_is_manifest_persist_error(self) -> None:
        """Given refresh_xml: When the post-Import-XML urgent save fails: Then
        ManifestPersistError is raised and Import XML remains on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            el = ET.Element("TRACK", {"Name": "Old", "Artist": "DJ"})
            loc = encode_location(dest)
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=loc,
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            collection = output_root.find("COLLECTION")
            assert collection is not None
            ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": "99",
                    "Name": "Old",
                    "Artist": "DJ",
                    "Location": loc,
                    "Kind": "WAV File",
                },
            )
            playlists = output_root.find("PLAYLISTS")
            assert playlists is not None
            root_node = playlists.find("NODE")
            assert root_node is not None
            playlist = ET.SubElement(
                root_node,
                "NODE",
                {
                    "Name": "P [WAV]",
                    "Type": "1",
                    "KeyType": "0",
                    "Entries": "1",
                },
            )
            ET.SubElement(playlist, "TRACK", {"Key": "99"})
            manifest = converter_manifest.empty_manifest()
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=source_root,
                output_root=output_root,
                output_existed=True,
                manifest=manifest,
            )
            bind_complete_assignment(manifest, item, "WAV/A.wav")
            el.set("Name", "New")
            prepared = PreparedConversion(
                plans=[plan],
                items=[item],
                manifest=manifest,
                preview=ConversionPreview(
                    selected=1,
                    resolved=1,
                    unique_outputs=1,
                    duplicates=0,
                    missing=0,
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
            )

            real_save = converter_manifest.save_manifest_tracks
            saves = {"n": 0}

            def tracking_save(tracks, wav_dir):
                saves["n"] += 1
                # prebatch + final succeed; post-XML urgent fails
                if saves["n"] >= 3:
                    raise OSError("post-xml metadata save failed")
                real_save(tracks, wav_dir)

            with patch.object(
                xml_output, "probe_dest_tech", return_value=("100", "1411", "44100")
            ), patch.object(
                converter_manifest, "save_manifest_tracks", side_effect=tracking_save
            ):
                with self.assertRaises(ManifestPersistError):
                    execute_prepared(prepared, force=False)

            self.assertTrue(plan.output.is_file())

    def test_failed_import_xml_does_not_advance_aiff_update_metadata(self) -> None:
        """Given update_metadata: When Import XML writing fails: Then dest
        output stats are stored, the old metadata signature remains, and the
        next classify is still update_metadata."""
        import cdj_aiff
        from test_cdj_safe_aiff import write_pcm_aiff

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "AIFF" / "A.aiff"
            dest.parent.mkdir()
            write_pcm_aiff(dest)
            el = ET.Element("TRACK", {"Name": "Old", "Artist": "DJ"})
            cdj_aiff.write_aiff_id3(dest, el, None, bit_depth=16, sample_rate=44100)
            loc = encode_location(dest)
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=loc,
                dest_name=dest.name,
                codec="pcm_s16be",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="aiff",
            )
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            collection = output_root.find("COLLECTION")
            assert collection is not None
            ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": "99",
                    "Name": "Old",
                    "Artist": "DJ",
                    "Location": loc,
                    "Kind": "AIFF File",
                },
            )
            playlists = output_root.find("PLAYLISTS")
            assert playlists is not None
            root_node = playlists.find("NODE")
            assert root_node is not None
            playlist = ET.SubElement(
                root_node,
                "NODE",
                {
                    "Name": "P [AIFF]",
                    "Type": "1",
                    "KeyType": "0",
                    "Entries": "1",
                },
            )
            ET.SubElement(playlist, "TRACK", {"Key": "99"})
            manifest = converter_manifest.empty_manifest()
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [AIFF]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=source_root,
                output_root=output_root,
                output_existed=True,
                output_format="aiff",
                manifest=manifest,
            )
            bind_complete_assignment(manifest, item, "AIFF/A.aiff")
            previous_sig = manifest.tracks[source_key(src)]["aiff"]["metadata"][
                "signature"
            ]
            previous_output = dict(manifest.tracks[source_key(src)]["aiff"]["output"])
            el.set("Name", "New")
            prepared = PreparedConversion(
                plans=[plan],
                items=[item],
                manifest=manifest,
                preview=ConversionPreview(
                    selected=1,
                    resolved=1,
                    unique_outputs=1,
                    duplicates=0,
                    missing=0,
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
            )

            def fail_xml(_root, _path) -> None:
                raise CliError("import xml write failed")

            with patch.object(
                xml_output, "probe_dest_tech", return_value=("100", "1411", "44100")
            ), patch.object(xml_output, "write_import_xml", side_effect=fail_xml):
                with self.assertRaises(CliError):
                    execute_prepared(prepared, force=False)
            self.assertFalse(plan.output.exists())
            loaded = converter_manifest.load_manifest(root)
            record = loaded.tracks[source_key(src)]["aiff"]
            self.assertEqual(record["metadata"]["signature"], previous_sig)
            self.assertNotEqual(previous_sig, metadata_signature(el))
            self.assertNotEqual(record["output"], previous_output)
            self.assertEqual(record["output"], output_signature(dest))
            plan.manifest = loaded
            self.assertEqual(
                classify_item(plan, item, False).action, "update_metadata"
            )

    def test_conflict_skips_xml_refresh_and_keeps_playlist_key(self) -> None:
        """Given a complete dest that was modified on disk: When execute:
        Then dest is not overwritten, Import XML Name is unchanged, and the
        existing playlist Key stays."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            prior = dest.read_bytes()
            el = ET.Element("TRACK", {"Name": "Old", "Artist": "DJ"})
            loc = encode_location(dest)
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location=loc,
                dest_name=dest.name,
                codec="pcm_s16le",
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            collection = output_root.find("COLLECTION")
            assert collection is not None
            ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": "99",
                    "Name": "Old",
                    "Artist": "DJ",
                    "Location": loc,
                    "Kind": "WAV File",
                },
            )
            playlists = output_root.find("PLAYLISTS")
            assert playlists is not None
            root_node = playlists.find("NODE")
            assert root_node is not None
            playlist = ET.SubElement(
                root_node,
                "NODE",
                {
                    "Name": "P [WAV]",
                    "Type": "1",
                    "KeyType": "0",
                    "Entries": "1",
                },
            )
            ET.SubElement(playlist, "TRACK", {"Key": "99"})
            manifest = converter_manifest.empty_manifest()
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=source_root,
                output_root=output_root,
                output_existed=True,
                manifest=manifest,
            )
            bind_complete_assignment(manifest, item, "WAV/A.wav")
            el.set("Name", "New")
            st = dest.stat()
            os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            prepared = PreparedConversion(
                plans=[plan],
                items=[item],
                manifest=manifest,
                preview=ConversionPreview(
                    selected=1,
                    resolved=1,
                    unique_outputs=1,
                    duplicates=0,
                    missing=0,
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
            )
            encoded: list[Path] = []

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                encoded.append(dest_path)
                dest_path.write_bytes(b"OVERWRITTEN")

            with patch.object(
                convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("100", "1411", "44100")
            ):
                stats = execute_prepared(prepared, force=False)
            self.assertEqual(encoded, [])
            self.assertEqual(
                stats.conflicts, [f"{src.name} → {dest.parent.name}/{dest.name}"]
            )
            self.assertEqual(dest.read_bytes(), prior)
            written = ET.parse(plan.output).getroot()
            tracks = written.findall("COLLECTION/TRACK")
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].get("TrackID"), "99")
            self.assertEqual(tracks[0].get("Name"), "Old")
            keys = [
                node.get("Key")
                for node in written.findall("PLAYLISTS/NODE/NODE/TRACK")
            ]
            self.assertEqual(keys, ["99"])

    def test_execute_prepared_persists_complete_signatures(self) -> None:
        """Given a first-run convert: When execute_prepared finishes: Then each
        written dest has a complete v2 record matching source and output stats."""
        encoded: list[str] = []

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            encoded.append(Path(source).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ):
            prepared, errors = prepare_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
            )
            self.assertEqual(errors, [])
            assert prepared is not None
            stats = execute_prepared(prepared, force=False, progress=False)

        self.assertEqual(stats.converted, 3)
        self.assertEqual(stats.recreated, 0)
        self.assertEqual(len(encoded), 3)
        loaded = converter_manifest.load_manifest(self.wav_dir)
        for item in prepared.items:
            record = loaded.tracks[source_key(item.source_path)]["wav"]
            self.assertEqual(assignment_state(record), "complete")
            self.assertEqual(record["source"], source_signature(item.source_path))
            self.assertEqual(record["output"], output_signature(item.dest_path))
            self.assertEqual(record["dest"], item.dest_path.relative_to(self.wav_dir).as_posix())

    def test_execute_prepared_saves_incomplete_before_mutating(self) -> None:
        """Given planned recreates: When execute_prepared runs: Then the first
        manifest save marks those assignments incomplete before ffmpeg writes."""
        saves: list[dict] = []
        real_save = converter_manifest.save_manifest_tracks

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
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ), patch.object(
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
            execute_prepared(prepared, force=False, progress=False)

        self.assertGreaterEqual(len(saves), 2)
        first = saves[0]
        for formats in first.values():
            record = formats["wav"]
            self.assertEqual(record.get("state"), "incomplete")
        last = saves[-1]
        for formats in last.values():
            self.assertEqual(assignment_state(formats["wav"]), "complete")

    def test_execute_prepared_checkpoints_manifest_during_batch(self) -> None:
        """Given several recreates: When execute_prepared runs with
        checkpoint_interval_s and a fake clock that advances past the interval:
        Then the manifest is saved between pre-batch and final."""
        saves: list[dict] = []
        real_save = converter_manifest.save_manifest_tracks
        clock = {"t": 0.0}

        def now() -> float:
            return clock["t"]

        def tracking_save(tracks, wav_dir):
            saves.append(deepcopy(tracks))
            real_save(tracks, wav_dir)

        def fake_ffmpeg(
            source: Path, dest: Path, codec: str, force: bool, **_kwargs
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")
            clock["t"] += 10.0

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ), patch.object(
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
                clock=now,
            )

        self.assertGreaterEqual(len(saves), 3)
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
        self.assertTrue(any(0 < n < 3 for n in completes[1:-1]))

    def test_checkpoint_keeps_unfinished_complete_tracks_incomplete(self) -> None:
        """Given two complete dests forced to rebuild: When the second write
        replaces dest then fails before complete persist: Then disk still marks
        that assignment incomplete and classify is a rebuild, not a conflict."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items: list[PlannedTrack] = []
            for name, pcm in (("A", b"\x11"), ("B", b"\x22")):
                src = root / f"{name}.flac"
                src.write_bytes(b"fLaC" + pcm)
                dest = root / "WAV" / f"{name}.wav"
                dest.parent.mkdir(parents=True, exist_ok=True)
                write_pcm_wav(dest)
                el = ET.Element("TRACK", {"Name": name, "Artist": "DJ"})
                item = PlannedTrack(
                    source_el=el,
                    source_path=src,
                    dest_path=dest,
                    dest_location=encode_location(dest),
                    dest_name=dest.name,
                    codec="pcm_s16le",
                    passthrough=False,
                    noop=False,
                    bit_depth=16,
                    sample_rate=44100,
                    output_format="wav",
                )
                items.append(item)
            manifest = converter_manifest.empty_manifest()
            bind_complete_assignment(manifest, items[0], "WAV/A.wav")
            bind_complete_assignment(manifest, items[1], "WAV/B.wav")
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=root / "WAV",
                output=root / "o.xml",
                tracks=list(items),
                unique=list(items),
                source_root=source_root,
                output_root=output_root,
                output_existed=False,
                manifest=manifest,
            )
            prepared = PreparedConversion(
                plans=[plan],
                items=list(items),
                manifest=manifest,
                preview=ConversionPreview(
                    selected=2,
                    resolved=2,
                    unique_outputs=2,
                    duplicates=0,
                    missing=0,
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
            )
            writes = {"n": 0}
            clock = {"t": 0.0}

            def now() -> float:
                return clock["t"]

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(b"NEW-" + source.name.encode())
                writes["n"] += 1
                clock["t"] += 10.0
                if writes["n"] >= 2:
                    raise RuntimeError("crash after replace")

            with patch.object(
                convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("4", "1411", "44100")
            ), patch.object(
                convert.plan, "default_convert_workers", return_value=1
            ):
                stats = execute_prepared(
                    prepared,
                    force=True,
                    progress=False,
                    workers=1,
                    checkpoint_interval_s=10.0,
                    clock=now,
                )
            self.assertTrue(stats.errors)
            loaded = converter_manifest.load_manifest(root)
            rec_a = loaded.tracks[source_key(items[0].source_path)]["wav"]
            rec_b = loaded.tracks[source_key(items[1].source_path)]["wav"]
            self.assertEqual(assignment_state(rec_a), "complete")
            self.assertEqual(assignment_state(rec_b), "incomplete")
            plan.manifest = loaded
            self.assertEqual(classify_item(plan, items[1], False).action, "transcode")

    def test_partial_failure_then_rerun_converges_complete(self) -> None:
        """Given a batch that leaves one assignment incomplete: When a second
        execute_prepared run finishes: Then both records are complete, dests
        exist, and Import XML is written."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items: list[PlannedTrack] = []
            for name, pcm in (("A", b"\x11"), ("B", b"\x22")):
                src = root / f"{name}.flac"
                src.write_bytes(b"fLaC" + pcm)
                dest = root / "WAV" / f"{name}.wav"
                dest.parent.mkdir(parents=True, exist_ok=True)
                write_pcm_wav(dest)
                el = ET.Element("TRACK", {"Name": name, "Artist": "DJ"})
                item = PlannedTrack(
                    source_el=el,
                    source_path=src,
                    dest_path=dest,
                    dest_location=encode_location(dest),
                    dest_name=dest.name,
                    codec="pcm_s16le",
                    passthrough=False,
                    noop=False,
                    bit_depth=16,
                    sample_rate=44100,
                    output_format="wav",
                )
                items.append(item)
            manifest = converter_manifest.empty_manifest()
            bind_complete_assignment(manifest, items[0], "WAV/A.wav")
            bind_complete_assignment(manifest, items[1], "WAV/B.wav")
            source_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
            ET.SubElement(
                source_root,
                "PRODUCT",
                {"Name": "rekordbox", "Version": "6.8.5", "Company": "AlphaTheta"},
            )
            output_root = skeleton_from(source_root)
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=root / "WAV",
                output=root / "o.xml",
                tracks=list(items),
                unique=list(items),
                source_root=source_root,
                output_root=output_root,
                output_existed=False,
                manifest=manifest,
            )
            prepared = PreparedConversion(
                plans=[plan],
                items=list(items),
                manifest=manifest,
                preview=ConversionPreview(
                    selected=2,
                    resolved=2,
                    unique_outputs=2,
                    duplicates=0,
                    missing=0,
                ),
                library_dir=root,
                output=plan.output,
                skipped=[],
            )
            crash = {"on": True, "n": 0}
            clock = {"t": 0.0}

            def now() -> float:
                return clock["t"]

            def fake_ffmpeg(
                source: Path, dest_path: Path, codec: str, force: bool, **_kwargs
            ) -> None:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(b"NEW-" + source.name.encode())
                crash["n"] += 1
                clock["t"] += 10.0
                if crash["on"] and crash["n"] >= 2:
                    raise RuntimeError("crash after replace")

            with patch.object(
                convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg
            ), patch.object(
                xml_output, "probe_dest_tech", return_value=("4", "1411", "44100")
            ), patch.object(
                convert.plan, "default_convert_workers", return_value=1
            ):
                first = execute_prepared(
                    prepared,
                    force=True,
                    progress=False,
                    workers=1,
                    checkpoint_interval_s=10.0,
                    clock=now,
                )
                self.assertTrue(first.errors)
                loaded = converter_manifest.load_manifest(root)
                self.assertEqual(
                    assignment_state(
                        loaded.tracks[source_key(items[1].source_path)]["wav"]
                    ),
                    "incomplete",
                )
                crash["on"] = False
                crash["n"] = 0
                clock["t"] = 0.0
                plan.manifest = loaded
                prepared.manifest = loaded
                prepared.decisions = {}
                second = execute_prepared(
                    prepared,
                    force=False,
                    progress=False,
                    workers=1,
                    checkpoint_interval_s=10.0,
                    clock=now,
                )
            self.assertEqual(second.errors, [])
            final = converter_manifest.load_manifest(root)
            for item in items:
                rec = final.tracks[source_key(item.source_path)]["wav"]
                self.assertEqual(assignment_state(rec), "complete")
                self.assertTrue(item.dest_path.is_file())
                self.assertEqual(classify_item(plan, item, False).action, "reuse")
            self.assertTrue(plan.output.is_file())
            written = ET.parse(plan.output).getroot()
            self.assertEqual(len(written.findall("COLLECTION/TRACK")), 2)


if __name__ == "__main__":
    unittest.main()
