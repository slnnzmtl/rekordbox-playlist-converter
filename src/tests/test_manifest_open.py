#!/usr/bin/env python3
"""Load/validation and fingerprint tests for DDD-154 Slice D."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import converter_manifest as cm


class ManifestLoadValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wav_dir = Path(self.tmp.name) / "lib"
        self.wav_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_adopts_tracks_without_deepcopy(self) -> None:
        """Given valid JSON on disk: When load_manifest runs: Then the
        in-memory tracks object is the parsed tracks (no per-record copy)."""
        payload = {
            "version": 2,
            "layout": "format-flat",
            "tracks": {"/music/a.flac": {"wav": {"dest": "WAV/Artist - Track.wav"}}},
        }
        (self.wav_dir / cm.MANIFEST_NAME).write_text("{}", encoding="utf-8")
        with patch.object(cm.json, "loads", return_value=payload):
            loaded = cm.load_manifest(self.wav_dir)
        self.assertIs(loaded.tracks, payload["tracks"])
        self.assertEqual(
            loaded.owner_for_dest("WAV/Artist - Track.wav"),
            ("/music/a.flac", "wav"),
        )

    def test_validate_passes_shared_library_root_to_each_dest(self) -> None:
        """Given several assignments: When validate_manifest_data runs: Then
        each dest check receives the same pre-resolved library_root."""
        data = {
            "version": 2,
            "layout": "format-flat",
            "tracks": {
                f"/s{i}": {"wav": {"dest": f"WAV/T{i}.wav"}} for i in range(5)
            },
        }
        roots: list[Path] = []
        real = cm.resolve_dest_under_wav_dir

        def tracking(wav_dir, relative_dest, *, library_root=None):
            roots.append(library_root)
            return real(wav_dir, relative_dest, library_root=library_root)

        with patch.object(cm, "resolve_dest_under_wav_dir", side_effect=tracking):
            errors = cm.validate_manifest_data(data, self.wav_dir)
        self.assertEqual(errors, [])
        self.assertEqual(len(roots), 5)
        self.assertTrue(all(r is roots[0] for r in roots))
        self.assertEqual(roots[0], cm.abs_path(self.wav_dir).resolve())

    def test_root_caching_still_rejects_symlink_escape(self) -> None:
        """Given a dest that resolves outside wav_dir via symlink: When
        validate: Then it is rejected even with root caching."""
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.wav").write_bytes(b"x")
        link = self.wav_dir / "WAV"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not available")
        data = {
            "version": 2,
            "layout": "format-flat",
            "tracks": {"/s": {"wav": {"dest": "WAV/secret.wav"}}},
        }
        errors = cm.validate_manifest_data(data, self.wav_dir)
        self.assertTrue(
            any("outside" in e.lower() or "resolves" in e.lower() for e in errors),
            errors,
        )

    def test_open_library_does_not_pair_stale_tracks_with_replaced_file_hash(
        self,
    ) -> None:
        """Given a valid manifest: When the path is atomically replaced after
        JSON parse: Then the returned fingerprint describes the parsed tracks,
        not the replacement file."""
        path = self.wav_dir / cm.MANIFEST_NAME
        body_a = (
            json.dumps(
                {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/a": {"wav": {"dest": "WAV/A.wav"}}},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        body_b = (
            json.dumps(
                {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/b": {"wav": {"dest": "WAV/B.wav"}}},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        path.write_text(body_a, encoding="utf-8")
        real_loads = json.loads

        def loads_then_replace(raw, *args, **kwargs):
            data = real_loads(raw, *args, **kwargs)
            tmp = path.with_name(path.name + ".new")
            tmp.write_text(body_b, encoding="utf-8")
            tmp.replace(path)
            return data

        with patch.object(cm.json, "loads", side_effect=loads_then_replace):
            opened = cm.open_library(self.wav_dir)
        self.assertIsNone(opened.error)
        assert opened.manifest is not None
        assert opened.fingerprint is not None
        self.assertIn("/a", opened.manifest.tracks)
        self.assertNotIn("/b", opened.manifest.tracks)
        self.assertEqual(
            opened.fingerprint.sha256,
            hashlib.sha256(body_a.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(opened.fingerprint.size, len(body_a.encode("utf-8")))

    def test_open_library_does_not_pair_empty_tracks_with_new_file_fingerprint(
        self,
    ) -> None:
        """Given no manifest: When a file appears after the absence probe: Then
        open_library does not return empty tracks with that file's fingerprint."""
        path = self.wav_dir / cm.MANIFEST_NAME
        self.assertFalse(path.exists())
        newer = cm.empty_manifest()
        newer.set_dest("/new", "wav", "WAV/New.wav")
        probes = {"n": 0}
        real_is_file = Path.is_file
        wav_dir = self.wav_dir

        def is_file_then_create(self: Path) -> bool:
            try:
                same = self == path or self.resolve() == path.resolve()
            except OSError:
                same = self == path
            if same:
                probes["n"] += 1
                if probes["n"] == 1:
                    return False
                if not path.exists():
                    cm.save_manifest(newer, wav_dir)
            return real_is_file(self)

        with patch.object(Path, "is_file", is_file_then_create):
            opened = cm.open_library(self.wav_dir)
        self.assertIsNone(opened.error)
        assert opened.manifest is not None
        assert opened.fingerprint is not None
        self.assertIn("/new", opened.manifest.tracks)
        self.assertNotEqual(list(opened.manifest.tracks), [])
        self.assertTrue(opened.fingerprint.exists)
        self.assertTrue(opened.fingerprint.matches_disk())
        self.assertEqual(
            opened.manifest.get_dest("/new", "wav"),
            "WAV/New.wav",
        )

    def test_open_library_fails_when_manifest_mutates_during_read(self) -> None:
        """Given a valid manifest: When fd stats change during the read: Then
        open_library reports an error instead of pairing mixed content."""
        m = cm.empty_manifest()
        m.set_dest("/music/a.flac", "wav", "WAV/Artist - Track.wav")
        cm.save_manifest(m, self.wav_dir)
        real_fstat = os.fstat

        def mutated_fstat(fd: int):
            st = real_fstat(fd)
            return type(
                "Stat",
                (),
                {
                    "st_dev": st.st_dev,
                    "st_ino": st.st_ino,
                    "st_size": st.st_size + 1,
                    "st_mtime_ns": st.st_mtime_ns,
                },
            )()

        with patch.object(cm.os, "fstat", side_effect=mutated_fstat):
            opened = cm.open_library(self.wav_dir)
        self.assertIsNotNone(opened.error)
        self.assertIsNone(opened.manifest)
        self.assertIn("changed while reading", (opened.error or "").lower())

    def test_open_library_returns_manifest_once(self) -> None:
        """Given a valid library: When open_library runs: Then error is None
        and a loaded manifest is returned with a fingerprint."""
        m = cm.empty_manifest()
        m.set_dest("/music/a.flac", "wav", "WAV/Artist - Track.wav")
        cm.save_manifest(m, self.wav_dir)
        opened = cm.open_library(self.wav_dir)
        self.assertIsNone(opened.error)
        assert opened.manifest is not None
        self.assertEqual(
            opened.manifest.get_dest("/music/a.flac", "wav"),
            "WAV/Artist - Track.wav",
        )
        assert opened.fingerprint is not None
        self.assertTrue(opened.fingerprint.exists)
        self.assertTrue(opened.fingerprint.matches_disk())

    def test_fingerprint_rejects_same_size_content_change(self) -> None:
        """Given a cached fingerprint: When manifest bytes change but size
        stays the same: Then matches_disk is False via SHA-256."""
        path = self.wav_dir / cm.MANIFEST_NAME
        body_a = (
            json.dumps(
                {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/a": {"wav": {"dest": "WAV/A.wav"}}},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        body_b = (
            json.dumps(
                {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/b": {"wav": {"dest": "WAV/B.wav"}}},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        # Pad so sizes match.
        if len(body_a) != len(body_b):
            pad = abs(len(body_a) - len(body_b))
            if len(body_a) < len(body_b):
                body_a = body_a[:-1] + (" " * pad) + "\n"
            else:
                body_b = body_b[:-1] + (" " * pad) + "\n"
        self.assertEqual(len(body_a), len(body_b))
        path.write_text(body_a, encoding="utf-8")
        fp = cm.ManifestFingerprint.capture(path)
        self.assertTrue(fp.matches_disk())
        path.write_text(body_b, encoding="utf-8")
        # Keep mtime/size if possible — SHA-256 must still disagree.
        self.assertEqual(path.stat().st_size, fp.size)
        self.assertFalse(fp.matches_disk())
        self.assertNotEqual(
            hashlib.sha256(body_a.encode()).hexdigest(),
            hashlib.sha256(body_b.encode()).hexdigest(),
        )

    def test_fingerprint_treats_absent_then_present_as_changed(self) -> None:
        """Given no manifest during capture: When a manifest appears later:
        Then matches_disk is False."""
        path = self.wav_dir / cm.MANIFEST_NAME
        fp = cm.ManifestFingerprint.capture(path)
        self.assertFalse(fp.exists)
        self.assertTrue(fp.matches_disk())
        path.write_text(
            json.dumps({"version": 2, "layout": "format-flat", "tracks": {}}),
            encoding="utf-8",
        )
        self.assertFalse(fp.matches_disk())

    def test_manifest_for_prepare_rejects_same_size_content_change(self) -> None:
        """Given a cached manifest: When on-disk bytes change at the same size:
        Then prepare reloads instead of reusing the cache."""
        path = self.wav_dir / cm.MANIFEST_NAME
        body_a = (
            json.dumps(
                {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/a": {"wav": {"dest": "WAV/A.wav"}}},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        body_b = (
            json.dumps(
                {
                    "version": 2,
                    "layout": "format-flat",
                    "tracks": {"/b": {"wav": {"dest": "WAV/B.wav"}}},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if len(body_a) != len(body_b):
            pad = abs(len(body_a) - len(body_b))
            if len(body_a) < len(body_b):
                body_a = body_a[:-1] + (" " * pad) + "\n"
            else:
                body_b = body_b[:-1] + (" " * pad) + "\n"
        path.write_text(body_a, encoding="utf-8")
        opened = cm.open_library(self.wav_dir)
        self.assertIsNone(opened.error)
        path.write_text(body_b, encoding="utf-8")
        reused = cm.manifest_for_prepare(
            self.wav_dir,
            cached_manifest=opened.manifest,
            cached_fingerprint=opened.fingerprint,
        )
        self.assertIsNone(reused.error)
        assert reused.manifest is not None
        self.assertIsNot(reused.manifest, opened.manifest)
        self.assertIn("/b", reused.manifest.tracks)
        self.assertNotIn("/a", reused.manifest.tracks)

    def test_manifest_for_prepare_cache_hit_returns_deepcopy(self) -> None:
        """Given a fingerprint that still matches disk: When prepare reuses the
        cache: Then the returned manifest is a copy; mutating it leaves the
        cached snapshot unchanged."""
        m = cm.empty_manifest()
        m.set_dest("/music/a.flac", "wav", "WAV/Artist - Track.wav")
        cm.save_manifest(m, self.wav_dir)
        opened = cm.open_library(self.wav_dir)
        self.assertIsNone(opened.error)
        assert opened.manifest is not None
        assert opened.fingerprint is not None
        prepared = cm.manifest_for_prepare(
            self.wav_dir,
            cached_manifest=opened.manifest,
            cached_fingerprint=opened.fingerprint,
        )
        self.assertIsNone(prepared.error)
        assert prepared.manifest is not None
        self.assertIsNot(prepared.manifest, opened.manifest)
        prepared.manifest.set_dest(
            "/music/a.flac", "wav", "WAV/Artist - Other.wav"
        )
        self.assertEqual(
            opened.manifest.get_dest("/music/a.flac", "wav"),
            "WAV/Artist - Track.wav",
        )
        self.assertEqual(
            prepared.manifest.get_dest("/music/a.flac", "wav"),
            "WAV/Artist - Other.wav",
        )


class CliOpenLibraryReuseTests(unittest.TestCase):
    def test_run_convert_batch_passes_opened_manifest_into_prepare(self) -> None:
        """Given CLI convert: When run_convert_batch validates the library:
        Then prepare_batch receives the opened manifest (no second load)."""
        import rb_playlist_to_wav as rb

        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "lib"
            wav_dir.mkdir()
            xml = Path(tmp) / "in.xml"
            xml.write_text(
                '<?xml version="1.0"?><DJ_PLAYLISTS Version="1.0.0"/>',
                encoding="utf-8",
            )
            output = wav_dir / "rekordbox-import.xml"
            opened_manifest = cm.empty_manifest()
            opened = cm.OpenLibraryResult(
                error=None,
                manifest=opened_manifest,
                fingerprint=cm.ManifestFingerprint.capture(
                    wav_dir / cm.MANIFEST_NAME
                ),
            )
            captured: dict = {}

            def fake_prepare_batch(*args, **kwargs):
                captured["manifest"] = kwargs.get("manifest")
                return None, ["stop-after-prepare"]

            import xml.etree.ElementTree as ET

            with patch.object(cm, "open_library", return_value=opened), patch.object(
                rb, "load_dj_playlists", return_value=ET.Element("DJ_PLAYLISTS")
            ), patch.object(rb, "prepare_batch", side_effect=fake_prepare_batch):
                code = rb.run_convert_batch(
                    xml,
                    [(None, "Any")],
                    wav_dir,
                    output,
                    force=False,
                    dry_run=True,
                )
            self.assertEqual(code, 1)
            self.assertIs(captured.get("manifest"), opened_manifest)


if __name__ == "__main__":
    unittest.main()
