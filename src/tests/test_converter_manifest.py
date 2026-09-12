#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import converter_manifest as cm


class ManifestValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wav_dir = Path(self.tmp.name) / "lib"
        self.wav_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_rejects_duplicate_dest_ownership(self) -> None:
        """Given two sources claiming the same dest after collision_key: When
        validate: Then duplicate ownership is rejected."""
        data = {
            "version": 1,
            "layout": "format-flat",
            "tracks": {
                "/a": {"wav": {"dest": "WAV/Same - Intro.wav"}},
                "/b": {"wav": {"dest": "WAV/same - intro.wav"}},
            },
        }
        errors = cm.validate_manifest_data(data, self.wav_dir)
        self.assertTrue(any("duplicate dest ownership" in e for e in errors))

    def test_validate_rejects_bad_version_and_layout(self) -> None:
        """Given wrong version or layout: When validate: Then each is reported."""
        bad_version = {
            "version": 2,
            "layout": "format-flat",
            "tracks": {},
        }
        bad_layout = {
            "version": 1,
            "layout": "nested",
            "tracks": {},
        }
        missing_layout = {
            "version": 1,
            "tracks": {},
        }
        v_errs = cm.validate_manifest_data(bad_version, self.wav_dir)
        self.assertTrue(any("version" in e for e in v_errs))
        l_errs = cm.validate_manifest_data(bad_layout, self.wav_dir)
        self.assertTrue(any("layout" in e for e in l_errs))
        m_errs = cm.validate_manifest_data(missing_layout, self.wav_dir)
        self.assertTrue(any("layout" in e for e in m_errs))

    def test_validate_rejects_non_format_flat_dests(self) -> None:
        """Given dests that are not exactly WAV/<file>.wav or AIFF/<file>.aiff:
        When validate: Then each shape is rejected."""
        cases = [
            ("/abs/WAV/Track.wav", "wav"),
            ("WAV\\Track.wav", "wav"),
            ("./WAV/Track.wav", "wav"),
            ("WAV/../Track.wav", "wav"),
            ("WAV/nested/Track.wav", "wav"),
            ("Other/Track.wav", "wav"),
            ("WAV/Track.aiff", "wav"),
            ("AIFF/Track.wav", "aiff"),
            ("WAV/Track.wav", "aiff"),
            ("AIFF/nested/Track.aiff", "aiff"),
        ]
        for dest, fmt in cases:
            with self.subTest(dest=dest, fmt=fmt):
                data = {
                    "version": 1,
                    "layout": "format-flat",
                    "tracks": {"/src": {fmt: {"dest": dest}}},
                }
                errors = cm.validate_manifest_data(data, self.wav_dir)
                self.assertTrue(errors, f"expected errors for {dest!r}/{fmt}")

    def test_save_manifest_uses_hidden_name(self) -> None:
        """Given a valid assignment: When save: Then hidden MANIFEST_NAME is
        written atomically with layout, version, and dest."""
        m = cm.empty_manifest()
        m.set_dest("/music/a.flac", "wav", "WAV/Artist - Track.wav")
        cm.save_manifest(m, self.wav_dir)
        path = self.wav_dir / cm.MANIFEST_NAME
        self.assertEqual(cm.MANIFEST_NAME, ".rekordbox-converter-manifest.json")
        self.assertTrue(path.is_file())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["layout"], "format-flat")
        self.assertEqual(data["version"], 1)
        self.assertEqual(
            data["tracks"]["/music/a.flac"]["wav"]["dest"],
            "WAV/Artist - Track.wav",
        )
        self.assertEqual(cm.validate_manifest_data(data, self.wav_dir), [])


class LibraryFolderValidationTests(unittest.TestCase):
    def test_empty_or_missing_dir_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "lib"
            existing.mkdir()
            self.assertIsNone(cm.validate_library_folder(existing))
            self.assertIsNone(cm.validate_library_folder(Path(tmp) / "new-lib"))

    def test_legacy_audio_without_manifest_is_refused(self) -> None:
        """Given WAV/AIFF audio and no hidden manifest: When validate: Then
        refuse."""
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            for folder, name in (
                ("WAV", "Artist - One.wav"),
                ("AIFF", "Artist - Two.aiff"),
            ):
                dest = wav_dir / folder
                dest.mkdir(parents=True)
                (dest / name).write_bytes(b"RIFF")
            self.assertFalse((wav_dir / cm.MANIFEST_NAME).exists())
            err = cm.validate_library_folder(wav_dir)
            self.assertIsNotNone(err)
            self.assertIn("new empty output folder", err.lower())

    def test_legacy_import_xml_without_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            wav_dir.mkdir()
            (wav_dir / "rekordbox-import.xml").write_text(
                "<DJ_PLAYLISTS/>", encoding="utf-8"
            )
            err = cm.validate_library_folder(wav_dir)
            self.assertIsNotNone(err)
            self.assertIn("new empty output folder", err.lower())

    def test_valid_manifest_allows_existing_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            dest = wav_dir / "WAV"
            dest.mkdir(parents=True)
            (dest / "Artist - Name.wav").write_bytes(b"RIFF")
            (wav_dir / cm.MANIFEST_NAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "layout": "format-flat",
                        "tracks": {
                            "/music/a.flac": {
                                "wav": {"dest": "WAV/Artist - Name.wav"}
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(cm.validate_library_folder(wav_dir))

    def test_invalid_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            wav_dir.mkdir()
            (wav_dir / cm.MANIFEST_NAME).write_text("{bad", encoding="utf-8")
            err = cm.validate_library_folder(wav_dir)
            self.assertIsNotNone(err)
            self.assertIn("manifest", err.lower())


if __name__ == "__main__":
    unittest.main()
