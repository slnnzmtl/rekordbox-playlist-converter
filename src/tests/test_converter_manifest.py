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
        self.wav_dir = Path(self.tmp.name) / "WAV"
        self.wav_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_rejects_duplicate_dest_ownership(self) -> None:
        """Given two sources claiming the same dest after collision_key: When
        validate: Then duplicate ownership is rejected."""
        data = {
            "version": 1,
            "tracks": {
                "/a": {"wav": {"dest": "Same/Hits/Intro.wav"}},
                "/b": {"wav": {"dest": "same/hits/intro.wav"}},
            },
        }
        errors = cm.validate_manifest_data(data, self.wav_dir)
        self.assertTrue(any("duplicate dest ownership" in e for e in errors))


class LibraryFolderValidationTests(unittest.TestCase):
    def test_empty_existing_dir_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            wav_dir.mkdir()
            self.assertIsNone(cm.validate_library_folder(wav_dir))

    def test_missing_dir_with_writable_parent_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "new-lib"
            self.assertIsNone(cm.validate_library_folder(wav_dir))

    def test_legacy_audio_without_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            nested = wav_dir / "Old Playlist"
            nested.mkdir(parents=True)
            (nested / "track.wav").write_bytes(b"RIFF")
            err = cm.validate_library_folder(wav_dir)
            self.assertIsNotNone(err)
            self.assertIn("new empty output folder", err.lower())

    def test_legacy_import_xml_without_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            wav_dir.mkdir()
            (wav_dir / "rekordbox-import.xml").write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            err = cm.validate_library_folder(wav_dir)
            self.assertIsNotNone(err)
            self.assertIn("new empty output folder", err.lower())

    def test_valid_manifest_allows_existing_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            dest = wav_dir / "Artist" / "Album"
            dest.mkdir(parents=True)
            (dest / "Name.wav").write_bytes(b"RIFF")
            (wav_dir / cm.MANIFEST_NAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "tracks": {
                            "/music/a.flac": {"wav": {"dest": "Artist/Album/Name.wav"}}
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
