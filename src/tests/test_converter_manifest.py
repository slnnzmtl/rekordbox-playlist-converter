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

    def _write(self, data: object) -> Path:
        path = self.wav_dir / cm.MANIFEST_NAME
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_load_rejects_invalid_json(self) -> None:
        """Given corrupt JSON on disk: When load_manifest runs: Then ManifestError."""
        path = self.wav_dir / cm.MANIFEST_NAME
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(cm.ManifestError) as ctx:
            cm.load_manifest(self.wav_dir)
        self.assertIn("invalid converter manifest JSON", str(ctx.exception))

    def test_load_rejects_wrong_version_and_missing_tracks(self) -> None:
        """Given version≠1 or missing tracks: When validate: Then errors are reported."""
        self.assertTrue(
            any(
                "version" in e
                for e in cm.validate_manifest_data({"version": 2, "tracks": {}}, self.wav_dir)
            )
        )
        self.assertTrue(
            any(
                "tracks" in e
                for e in cm.validate_manifest_data({"version": 1}, self.wav_dir)
            )
        )

    def test_load_rejects_bad_keys_formats_and_dests(self) -> None:
        """Given empty keys, unknown formats, or unsafe dests: When validate:
        Then each class of problem is rejected."""
        cases = [
            {"version": 1, "tracks": {"": {"wav": {"dest": "A/B/C.wav"}}}},
            {"version": 1, "tracks": {"/src": {}}},
            {
                "version": 1,
                "tracks": {"/src": {"flac": {"dest": "A/B/C.wav"}}},
            },
            {
                "version": 1,
                "tracks": {"/src": {"wav": {"dest": "A\\B\\C.wav"}}},
            },
            {
                "version": 1,
                "tracks": {"/src": {"wav": {"dest": "../escape.wav"}}},
            },
            {
                "version": 1,
                "tracks": {"/src": {"wav": {"dest": "/absolute/x.wav"}}},
            },
            {
                "version": 1,
                "tracks": {"/src": {"wav": {"dest": "A/B/C.aiff"}}},
            },
        ]
        for data in cases:
            errors = cm.validate_manifest_data(data, self.wav_dir)
            self.assertTrue(errors, f"expected errors for {data!r}")

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

    def test_load_accepts_valid_per_format_assignments(self) -> None:
        """Given valid wav and aiff dests for one source: When load_manifest:
        Then both format assignments are available."""
        data = {
            "version": 1,
            "tracks": {
                "/music/track.flac": {
                    "wav": {"dest": "Artist/Album/Name.wav"},
                    "aiff": {"dest": "Artist/Album/Name.aiff"},
                }
            },
        }
        self._write(data)
        manifest = cm.load_manifest(self.wav_dir)
        self.assertEqual(
            manifest.get_dest("/music/track.flac", "wav"),
            "Artist/Album/Name.wav",
        )
        self.assertEqual(
            manifest.get_dest("/music/track.flac", "aiff"),
            "Artist/Album/Name.aiff",
        )


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
