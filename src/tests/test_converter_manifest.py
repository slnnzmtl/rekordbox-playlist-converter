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


if __name__ == "__main__":
    unittest.main()
