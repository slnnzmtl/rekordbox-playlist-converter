#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
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
import cdj_wav
import convert.plan
from convert import encode
from convert.plan import collect_batch_unique
from convert.write import convert_unique
import converter_manifest
import ffmpeg_tools
import xml_output
from convert_fixtures import (
    FIXTURE,
    XmlFixtureTests as XmlFixtureBase,
    flac_probe,
    wav_probe,
    write_flac,
)
from rekordbox_xml import encode_location, find_playlists_by_name
from xml_output import apply_xml, share_output_root, write_import_xml


class XmlFixtureTests(XmlFixtureBase):
    def test_missing_source_file_does_not_abort_convert(self) -> None:
        self.c.unlink()

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        stdout = io.StringIO()
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
        ), patch.object(sys, "stdout", stdout):
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
        pl = find_playlists_by_name(out, "Untitled Intelligent List [WAV]")
        self.assertEqual(len(pl), 0)
        printed = stdout.getvalue()
        self.assertIn("generated playlist was not created or refreshed", printed)
        self.assertNotIn("Import Playlist", printed)

    def test_invalid_manifest_fails_before_audio_or_xml(self) -> None:
        """Given a corrupt manifest on disk: When main converts: Then exit is
        nonzero, stderr mentions the manifest, and no audio/XML is written."""
        self.wav_dir.mkdir(parents=True)
        (self.wav_dir / converter_manifest.MANIFEST_NAME).write_text(
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

    def test_cli_refuses_legacy_playlist_dir_without_manifest(self) -> None:
        """Given format-flat WAV audio and no hidden manifest: When main
        converts: Then exit is nonzero, stderr asks for a new empty output
        folder, and no new audio/XML is written."""
        legacy = self.wav_dir / "WAV"
        legacy.mkdir(parents=True)
        (legacy / "Artist - Track.wav").write_bytes(b"RIFF")
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
        err = stderr.getvalue().casefold()
        self.assertIn("new empty output folder", err)
        self.assertFalse(self.output.exists())
        self.assertFalse(
            (self.wav_dir / converter_manifest.MANIFEST_NAME).exists()
        )

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
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
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
            share_output_root(plans)
            self.assertIs(plan_a.output_root, plan_b.output_root)
            items = collect_batch_unique(plans)
            self.assertEqual(len(items), 3)
            stats = convert_unique(plan_a, force=False, progress=False, items=items)
            for plan in plans:
                apply_xml(plan, stats.succeeded)
            write_import_xml(plan_a.output_root, plan_a.output)

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
        morning_pl = find_playlists_by_name(out, "Morning [WAV]")[0]
        self.assertEqual(len(morning_pl.findall("TRACK")), 1)
        bestial = [t for t in out.findall("COLLECTION/TRACK") if t.get("Name") == "Bestial"]
        self.assertEqual(len(bestial), 1)
        tid = bestial[0].get("TrackID")
        for pl_name in ("Untitled Intelligent List [WAV]", "Morning [WAV]"):
            pl = find_playlists_by_name(out, pl_name)[0]
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
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ), patch.object(sys.stdin, "isatty", return_value=True), patch.object(
            rb, "prompt_wizard", return_value=wizard
        ):
            self.assertEqual(rb.main([]), 0)

        self.assertEqual(len(encoded), 3)
        self.assertEqual(encoded.count("07 - Bestial.flac"), 1)
        dest = self.wav_dir / "WAV" / "ABSL - Bestial.wav"
        self.assertTrue(dest.is_file())
        out = ET.parse(self.output).getroot()
        self.assertEqual(len(out.findall("COLLECTION/TRACK")), 3)
        bestial = [t for t in out.findall("COLLECTION/TRACK") if t.get("Name") == "Bestial"]
        self.assertEqual(len(bestial), 1)
        tid = bestial[0].get("TrackID")
        for pl_name in ("Untitled Intelligent List [WAV]", "Morning [WAV]"):
            pl = find_playlists_by_name(out, pl_name)[0]
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
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(
            cdj_wav, "is_cdj_safe_wav", return_value=False
        ), patch.object(sys.stdin, "isatty", return_value=True), patch.object(
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
            patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg),
            patch.object(encode, "is_cdj_safe_wav", return_value=False),
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

        # Drop last track from source playlist; re-run must rewrite playlist to match.
        src = ET.parse(self.xml_path).getroot()
        node = find_playlists_by_name(src, "Untitled Intelligent List")[0]
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
        pl = find_playlists_by_name(out, "Untitled Intelligent List [WAV]")[0]
        self.assertEqual([t.get("Key") for t in pl.findall("TRACK")], ["1", "2"])

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
                "Location": encode_location(d),
                "BitRate": "0",
                "SampleRate": "44100",
            },
        )
        node = find_playlists_by_name(src, "Untitled Intelligent List")[0]
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
        pl = find_playlists_by_name(out, "Untitled Intelligent List [WAV]")[0]
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
        """Given --dry-run with one missing source: When main runs: Then nothing
        is written, and stdout reports ConversionPreview counts, format dir,
        each unique input filename/action/quality/size, missing warnings,
        playlist name, and Import XML path."""
        self.c.unlink()
        duration = 10.0

        def probe_with_duration(path: Path, **_kwargs: object) -> dict:
            data = self._probe(path)
            if path.suffix.lower() != ".wav":
                data = {
                    "format": {
                        "format_name": "flac",
                        "duration": str(duration),
                    },
                    "streams": list(data["streams"]),
                }
            return data

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=probe_with_duration
        ), patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
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
        self.assertFalse((self.wav_dir / converter_manifest.MANIFEST_NAME).exists())
        self.assertFalse((self.wav_dir / "WAV").exists())
        self.assertFalse((self.wav_dir / "AIFF").exists())
        self.assertFalse((self.root / "WAV" / converter_manifest.MANIFEST_NAME).exists())
        if self.wav_dir.exists():
            leftover = [
                p
                for p in self.wav_dir.rglob("*")
                if p.is_file()
                and (
                    p.suffix.casefold() in {".wav", ".aiff", ".aif", ".xml", ".tmp", ".json"}
                    or p.name.startswith(".")
                    or ".tmp" in p.name
                    or p.name.endswith("~")
                )
            ]
            self.assertEqual(leftover, [], f"dry-run left artifacts: {leftover}")
        # Parent of wav_dir must not gain WAV/AIFF audio or converter temps either.
        audio_or_sidecar = [
            p
            for p in self.root.rglob("*")
            if p.is_file()
            and p != self.xml_path
            and not str(p).startswith(str(self.music))
            and (
                p.suffix.casefold() in {".wav", ".aiff", ".aif", ".xml"}
                or p.name == converter_manifest.MANIFEST_NAME
                or ".manifest-" in p.name
                or p.suffix.casefold() == ".tmp"
                or p.name.endswith(".tmp.json")
            )
        ]
        self.assertEqual(
            audio_or_sidecar,
            [],
            f"dry-run wrote under output root: {audio_or_sidecar}",
        )

        stdout = out_buf.getvalue()
        stderr = err_buf.getvalue()
        # summary counts (2 resolved + 1 missing; 2 unique; 0 duplicates)
        self.assertIn("2 unique output file(s)", stdout)
        self.assertIn("3 selected", stdout)
        self.assertIn("2 resolved", stdout)
        self.assertIn("0 duplicate(s)", stdout)
        self.assertIn("1 missing", stdout)
        # selected format directory
        format_dir = str(self.wav_dir / "WAV")
        self.assertIn(format_dir, stdout)
        # unique input filenames + dest + format + action label + reason + write kind + quality + size
        for src in (self.a, self.b):
            self.assertIn(src.name, stdout)
        self.assertNotIn(str(self.a), stdout)
        self.assertNotIn(self.c.name, stdout)
        self.assertIn("WAV/ABSL - Bestial.wav", stdout)
        self.assertIn("WAV", stdout)
        self.assertIn("Recreate missing", stdout)
        self.assertNotIn("recreate_missing", stdout)
        self.assertIn("Destination file is missing", stdout)
        self.assertIn("writes audio", stdout)
        self.assertIn("24-bit / 44.1 kHz", stdout)
        self.assertIn("≈ 2.5 MB", stdout)
        # missing-source warning
        self.assertIn("missing source file", stderr)
        self.assertIn(str(self.c), stderr)
        # resulting playlist name + Import XML path
        self.assertIn("Generated playlist:", stdout)
        self.assertNotIn("New playlist:", stdout)
        self.assertIn("Untitled Intelligent List [WAV]", stdout)
        self.assertIn(str(self.output), stdout)

    def test_cli_refuses_write_when_preview_has_conflicts(self) -> None:
        """Given a conflict in the prepared preview: When converting (not dry-run):
        Then preview_block_message is printed and nothing is written."""
        from convert.models import (
            ConversionPreview,
            ConversionPreviewItem,
            Plan,
            PreparedConversion,
        )
        import convert.write as convert_write

        prepared_preview = ConversionPreview(
            selected=1,
            resolved=1,
            unique_outputs=1,
            duplicates=0,
            missing=0,
            items=[
                ConversionPreviewItem(
                    relative_dest="WAV/A.wav",
                    action="external_modification_conflict",
                    bit_depth=16,
                    sample_rate=44100,
                    size_bytes=1000,
                    size_display="0.0 MB",
                    source_display="a.flac",
                    reason="Destination was changed outside this app",
                    write_kind="none",
                    output_format="wav",
                ),
            ],
        )

        def fake_prepare(*_a, **_k):
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=self.wav_dir,
                media_dir=self.wav_dir / "WAV",
                output=self.output,
                tracks=[],
                unique=[],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
            )
            prepared = PreparedConversion(
                plans=[plan],
                items=[],
                manifest=converter_manifest.empty_manifest(),
                preview=prepared_preview,
                library_dir=self.wav_dir,
                output=self.output,
                skipped=[],
            )
            return prepared, []

        err_buf = io.StringIO()
        with patch.object(rb, "prepare_batch", side_effect=fake_prepare), patch(
            "sys.stderr", err_buf
        ), patch.object(convert_write, "execute_prepared") as execute:
            rc = rb.run_convert_batch(
                self.xml_path,
                [(None, "Untitled Intelligent List")],
                self.wav_dir,
                self.output,
                force=False,
                dry_run=False,
            )
        self.assertEqual(rc, 1)
        self.assertIn("unresolved conflict", err_buf.getvalue())
        execute.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_main_omitted_output_writes_import_xml_under_wav_dir(self) -> None:
        """Given no --output: When main converts: Then import XML is
        <wav_dir>/rekordbox-import.xml."""

        def fake_ffmpeg(source: Path, dest: Path, codec: str, force: bool, **_kwargs) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"RIFF")

        with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
            ffmpeg_tools, "run_ffprobe", side_effect=self._probe
        ), patch.object(convert.plan, "run_ffmpeg", side_effect=fake_ffmpeg), patch.object(cdj_wav, "is_cdj_safe_wav", return_value=False
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

        def probe(path: Path, **_kwargs: object) -> dict:
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
    <TRACK TrackID="1" Name="mp3" Location="{encode_location(mp3)}" Kind="MP3 File"/>
    <TRACK TrackID="2" Name="odd" Location="{encode_location(mystery)}" Kind="FLAC File"/>
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
        self.assertFalse(flac_items[0].passthrough)
        self.assertEqual(plan.max_bit_depth, 24)
        self.assertEqual(plan.max_sample_rate, 48000)

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



if __name__ == "__main__":
    unittest.main()
