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
            "version": 2,
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
            "version": 3,
            "layout": "format-flat",
            "tracks": {},
        }
        bad_layout = {
            "version": 2,
            "layout": "nested",
            "tracks": {},
        }
        missing_layout = {
            "version": 2,
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
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/src": {fmt: {"dest": dest}}},
                }
                errors = cm.validate_manifest_data(data, self.wav_dir)
                self.assertTrue(errors, f"expected errors for {dest!r}/{fmt}")

    def test_set_dest_keeps_existing_freshness_fields(self) -> None:
        """Given a complete record: When set_dest: Then dest updates and
        state, source, metadata, output, and recipe stay."""
        m = cm.empty_manifest()
        record = {
            "dest": "WAV/Artist - Track.wav",
            "state": "complete",
            "source": {"size": 10, "mtime_ns": 1, "hash": None},
            "metadata": {"signature": "sha256:" + ("ab" * 32)},
            "output": {"size": 20, "mtime_ns": 2, "hash": None},
            "recipe": {
                "format": "wav",
                "bit_depth": 24,
                "sample_rate": 48000,
                "channels": 2,
                "revision": 1,
            },
        }
        m.tracks["/music/a.flac"] = {"wav": record}
        m.set_dest("/music/a.flac", "wav", "WAV/Artist - Track.wav")
        kept = m.tracks["/music/a.flac"]["wav"]
        self.assertEqual(kept["dest"], "WAV/Artist - Track.wav")
        self.assertEqual(kept["state"], "complete")
        self.assertEqual(kept["source"], record["source"])
        self.assertEqual(kept["metadata"], record["metadata"])
        self.assertEqual(kept["output"], record["output"])
        self.assertEqual(kept["recipe"], record["recipe"])

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
        self.assertEqual(data["version"], 2)
        self.assertEqual(
            data["tracks"]["/music/a.flac"]["wav"]["dest"],
            "WAV/Artist - Track.wav",
        )
        self.assertEqual(cm.validate_manifest_data(data, self.wav_dir), [])

    def test_load_preserves_optional_freshness_fields(self) -> None:
        """Given a v2 record with dest plus freshness objects: When load: Then
        state, source, metadata, output, and recipe are kept."""
        record = {
            "dest": "WAV/Artist - Track.wav",
            "state": "complete",
            "source": {"size": 10, "mtime_ns": 1, "hash": None},
            "metadata": {
                "signature": "sha256:" + ("ab" * 32),
            },
            "output": {"size": 20, "mtime_ns": 2, "hash": None},
            "recipe": {
                "format": "wav",
                "bit_depth": 24,
                "sample_rate": 48000,
                "channels": 2,
                "revision": 1,
            },
        }
        payload = {
            "version": 2,
            "layout": "format-flat",
            "tracks": {"/music/a.flac": {"wav": record}},
        }
        (self.wav_dir / cm.MANIFEST_NAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )
        loaded = cm.load_manifest(self.wav_dir)
        self.assertEqual(loaded.tracks["/music/a.flac"]["wav"], record)

    def test_save_round_trip_is_deterministic(self) -> None:
        """Given a complete v2 assignment: When save then load: Then JSON is
        sorted and the record is unchanged."""
        record = {
            "dest": "WAV/Artist - Track.wav",
            "state": "complete",
            "source": {"size": 10, "mtime_ns": 1, "hash": None},
            "metadata": {"signature": "sha256:" + ("ab" * 32)},
            "output": {"size": 20, "mtime_ns": 2, "hash": None},
            "recipe": {
                "format": "wav",
                "bit_depth": 24,
                "sample_rate": 48000,
                "channels": 2,
                "revision": 1,
            },
        }
        m = cm.empty_manifest()
        m.tracks["/music/a.flac"] = {"wav": record}
        cm.save_manifest(m, self.wav_dir)
        path = self.wav_dir / cm.MANIFEST_NAME
        text = path.read_text(encoding="utf-8")
        canonical = (
            json.dumps(m.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
            + "\n"
        )
        self.assertEqual(text, canonical)
        self.assertEqual(
            cm.load_manifest(self.wav_dir).tracks["/music/a.flac"]["wav"],
            record,
        )

    def test_validate_rejects_unknown_keys(self) -> None:
        """Given extra keys on the record or nested freshness objects: When
        validate: Then unknown fields are reported."""
        base = {
            "dest": "WAV/Artist - Track.wav",
            "state": "complete",
            "source": {"size": 10, "mtime_ns": 1, "hash": None},
            "metadata": {"signature": "sha256:" + ("ab" * 32)},
            "output": {"size": 20, "mtime_ns": 2, "hash": None},
            "recipe": {
                "format": "wav",
                "bit_depth": 24,
                "sample_rate": 48000,
                "channels": 2,
                "revision": 1,
            },
        }
        cases = [
            ({**base, "playlist": "Night"}, "playlist"),
            ({**base, "source": {**base["source"], "path": "/x"}}, "path"),
            ({**base, "recipe": {**base["recipe"], "passthrough": False}}, "passthrough"),
        ]
        for record, needle in cases:
            with self.subTest(needle=needle):
                data = {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/music/a.flac": {"wav": record}},
                }
                errors = cm.validate_manifest_data(data, self.wav_dir)
                self.assertTrue(
                    any(needle in e for e in errors),
                    f"expected {needle!r} in {errors}",
                )

    def test_validate_rejects_malformed_hashes(self) -> None:
        """Given source or output hash that is not sha256: plus 64 hex: When
        validate: Then the hash is rejected; null and omitted hashes pass."""
        dest = "WAV/Artist - Track.wav"
        ok_none = {
            "dest": dest,
            "source": {"size": 1, "mtime_ns": 1, "hash": None},
        }
        ok_omitted = {
            "dest": dest,
            "source": {"size": 1, "mtime_ns": 1},
        }
        ok_sha = {
            "dest": dest,
            "source": {
                "size": 1,
                "mtime_ns": 1,
                "hash": "sha256:" + ("ab" * 32),
            },
        }
        for record in (ok_none, ok_omitted, ok_sha):
            data = {
                "version": 2,
                "layout": "format-flat",
                "tracks": {"/s": {"wav": record}},
            }
            self.assertEqual(cm.validate_manifest_data(data, self.wav_dir), [])
        bad_hashes = [
            "sha256:abcd",
            "sha256:" + ("zz" * 32),
            "md5:" + ("ab" * 32),
            "ab" * 32,
            12,
        ]
        for bad in bad_hashes:
            with self.subTest(hash=bad):
                record = {
                    "dest": dest,
                    "output": {"size": 1, "mtime_ns": 1, "hash": bad},
                }
                data = {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/s": {"wav": record}},
                }
                errors = cm.validate_manifest_data(data, self.wav_dir)
                self.assertTrue(any("hash" in e for e in errors), errors)

    def test_validate_rejects_bad_freshness_types(self) -> None:
        """Given malformed state, stats, or recipe values: When validate: Then
        each bad type is reported."""
        dest = "WAV/Artist - Track.wav"
        cases = [
            ({"dest": dest, "state": "done"}, "state"),
            ({"dest": dest, "source": {"size": "10", "mtime_ns": 1}}, "size"),
            ({"dest": dest, "source": {"size": 10, "mtime_ns": 1.5}}, "mtime"),
            (
                {
                    "dest": dest,
                    "recipe": {
                        "format": "mp3",
                        "bit_depth": 24,
                        "sample_rate": 48000,
                        "channels": 2,
                        "revision": 1,
                    },
                },
                "format",
            ),
            (
                {
                    "dest": dest,
                    "recipe": {
                        "format": "wav",
                        "bit_depth": 32,
                        "sample_rate": 48000,
                        "channels": 2,
                        "revision": 1,
                    },
                },
                "bit_depth",
            ),
        ]
        for record, needle in cases:
            with self.subTest(needle=needle):
                data = {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/s": {"wav": record}},
                }
                errors = cm.validate_manifest_data(data, self.wav_dir)
                self.assertTrue(
                    any(needle in e for e in errors),
                    f"expected {needle!r} in {errors}",
                )


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
                        "version": 2,
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

    def test_v1_manifest_is_refused_with_delete_or_new_folder(self) -> None:
        """Given an unreleased v1 manifest: When validate: Then refuse with
        instructions to delete it or choose a new output folder."""
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            wav_dir.mkdir()
            (wav_dir / cm.MANIFEST_NAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "layout": "format-flat",
                        "tracks": {},
                    }
                ),
                encoding="utf-8",
            )
            err = cm.validate_library_folder(wav_dir)
            self.assertIsNotNone(err)
            assert err is not None
            lowered = err.lower()
            self.assertIn("version 1", lowered)
            self.assertTrue(
                "delete" in lowered and "new" in lowered,
                err,
            )

    def test_unknown_and_future_versions_are_refused(self) -> None:
        """Given version 0 or 3: When validate: Then each is an unsupported
        version error naming the found version."""
        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            wav_dir.mkdir()
            for version in (0, 3):
                with self.subTest(version=version):
                    data = {
                        "version": version,
                        "layout": "format-flat",
                        "tracks": {},
                    }
                    errors = cm.validate_manifest_data(data, wav_dir)
                    joined = " ".join(errors)
                    self.assertIn("version", joined)
                    self.assertIn(repr(version), joined)


if __name__ == "__main__":
    unittest.main()
