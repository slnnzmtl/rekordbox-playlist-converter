#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import converter_manifest as cm
from cli_error import CliError
from rekordbox_xml import encode_location, iter_playlist_nodes
import import_edit


def _write_import_xml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _write_manifest(library: Path, tracks: dict) -> None:
    payload = {
        "version": 2,
        "layout": "format-flat",
        "tracks": tracks,
    }
    (library / cm.MANIFEST_NAME).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _valid_library(tmp: Path) -> Path:
    """Minimal valid Import XML + manifest with one owned WAV track."""
    library = tmp / "lib"
    (library / "WAV").mkdir(parents=True)
    wav = library / "WAV" / "Artist - Track.wav"
    wav.write_bytes(b"RIFF")
    loc = encode_location(wav)
    xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Track" Artist="Artist" Kind="WAV File"
           Location="{loc}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
    _write_import_xml(library / "rekordbox-import.xml", xml)
    _write_manifest(
        library,
        {"/source/a.flac": {"wav": {"dest": "WAV/Artist - Track.wav"}}},
    )
    return library


class LoadImportEditDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_returns_draft_for_valid_library(self) -> None:
        """Given Import XML + manifest with owned track: When load: Then draft
        exposes root, manifest, and fingerprints."""
        library = _valid_library(self.root)
        draft = import_edit.load_import_edit_draft(library)
        self.assertEqual(draft.root.tag, "DJ_PLAYLISTS")
        self.assertIn("/source/a.flac", draft.manifest.tracks)
        self.assertFalse(draft.dirty)
        self.assertTrue(draft.xml_fingerprint)
        self.assertTrue(draft.manifest_fingerprint)

    def test_load_refuses_missing_import_xml(self) -> None:
        library = self.root / "lib"
        library.mkdir()
        _write_manifest(library, {})
        with self.assertRaises(CliError) as ctx:
            import_edit.load_import_edit_draft(library)
        self.assertIn("Import XML", str(ctx.exception))

    def test_load_refuses_missing_manifest(self) -> None:
        library = _valid_library(self.root)
        (library / cm.MANIFEST_NAME).unlink()
        with self.assertRaises(CliError) as ctx:
            import_edit.load_import_edit_draft(library)
        self.assertIn("manifest", str(ctx.exception).casefold())

    def test_load_allows_dangling_track_id(self) -> None:
        """Given playlist Key with no COLLECTION row: When load: Then draft
        loads (recoverable missing entry)."""
        library = _valid_library(self.root)
        xml_path = library / "rekordbox-import.xml"
        text = xml_path.read_text(encoding="utf-8")
        text = text.replace(
            '<TRACK Key="1"/>',
            '<TRACK Key="1"/><TRACK Key="999"/>',
        )
        text = text.replace('Entries="1"', 'Entries="2"', 1)
        # Only the playlist Entries — keep COLLECTION Entries=1
        text = text.replace(
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1"',
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="2"',
        )
        xml_path.write_text(text, encoding="utf-8")
        draft = import_edit.load_import_edit_draft(library)
        self.assertEqual(draft.root.tag, "DJ_PLAYLISTS")

    def test_load_allows_repeated_playlist_key(self) -> None:
        """Given the same TrackID twice in one playlist: When load: Then draft
        loads (Rekordbox playlists may list a track more than once)."""
        library = _valid_library(self.root)
        xml_path = library / "rekordbox-import.xml"
        text = xml_path.read_text(encoding="utf-8")
        text = text.replace(
            '<TRACK Key="1"/>',
            '<TRACK Key="1"/><TRACK Key="1"/>',
        )
        text = text.replace(
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1"',
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="2"',
        )
        xml_path.write_text(text, encoding="utf-8")
        draft = import_edit.load_import_edit_draft(library)
        playlist = next(
            node
            for kind, _folder, name, node in iter_playlist_nodes(draft.root)
            if kind == "playlist" and name == "Night Set [WAV]"
        )
        self.assertEqual([e.get("Key") for e in playlist.findall("TRACK")], ["1", "1"])

    def test_load_refuses_blank_playlist_key(self) -> None:
        library = _valid_library(self.root)
        xml_path = library / "rekordbox-import.xml"
        text = xml_path.read_text(encoding="utf-8")
        text = text.replace('<TRACK Key="1"/>', '<TRACK Key=""/>')
        xml_path.write_text(text, encoding="utf-8")
        with self.assertRaises(CliError) as ctx:
            import_edit.load_import_edit_draft(library)
        self.assertIn("Key", str(ctx.exception))

    def test_load_refuses_unmanaged_collection_dest(self) -> None:
        library = _valid_library(self.root)
        orphan = library / "WAV" / "Orphan - Track.wav"
        orphan.write_bytes(b"RIFF")
        loc = encode_location(orphan)
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Track" Artist="Orphan" Kind="WAV File"
           Location="{loc}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        _write_import_xml(library / "rekordbox-import.xml", xml)
        # Manifest still points at a different dest; collection row is unmanaged.
        with self.assertRaises(CliError) as ctx:
            import_edit.load_import_edit_draft(library)
        self.assertRegex(str(ctx.exception).casefold(), r"unmanaged|owner|manifest")


class DestOwnerMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_wav_and_aiff_assignments_are_independent(self) -> None:
        """Given one source with WAV and AIFF dests: When resolve each collection
        row: Then each Kind matches its own format owner."""
        library = self.root / "lib"
        (library / "WAV").mkdir(parents=True)
        (library / "AIFF").mkdir(parents=True)
        wav = library / "WAV" / "Artist - Track.wav"
        aiff = library / "AIFF" / "Artist - Track.aiff"
        wav.write_bytes(b"RIFF")
        aiff.write_bytes(b"FORM")
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="Track" Artist="Artist" Kind="WAV File"
           Location="{encode_location(wav)}" SampleRate="44100"/>
    <TRACK TrackID="2" Name="Track" Artist="Artist" Kind="AIFF File"
           Location="{encode_location(aiff)}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Set [WAV]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
      <NODE Name="Set [AIFF]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        _write_import_xml(library / "rekordbox-import.xml", xml)
        _write_manifest(
            library,
            {
                "/source/a.flac": {
                    "wav": {"dest": "WAV/Artist - Track.wav"},
                    "aiff": {"dest": "AIFF/Artist - Track.aiff"},
                }
            },
        )
        draft = import_edit.load_import_edit_draft(library)
        owners = import_edit.build_dest_owner_index(draft.manifest)
        tracks = list(draft.root.find("COLLECTION").findall("TRACK"))
        wav_owner = import_edit.resolve_collection_owner(
            tracks[0], library_dir=library, owners=owners
        )
        aiff_owner = import_edit.resolve_collection_owner(
            tracks[1], library_dir=library, owners=owners
        )
        self.assertEqual(wav_owner.output_format, "wav")
        self.assertEqual(aiff_owner.output_format, "aiff")
        self.assertEqual(wav_owner.source_key, aiff_owner.source_key)

    def test_resolve_refuses_path_escape(self) -> None:
        library = _valid_library(self.root)
        draft = import_edit.load_import_edit_draft(library)
        owners = import_edit.build_dest_owner_index(draft.manifest)
        outside = self.root / "outside.wav"
        outside.write_bytes(b"RIFF")
        track = ET.Element(
            "TRACK",
            {
                "TrackID": "9",
                "Kind": "WAV File",
                "Location": encode_location(outside),
            },
        )
        with self.assertRaises(CliError) as ctx:
            import_edit.resolve_collection_owner(
                track, library_dir=library, owners=owners
            )
        self.assertIn("outside", str(ctx.exception).casefold())

    def test_load_refuses_symlink_collection_location(self) -> None:
        library = _valid_library(self.root)
        real = library / "WAV" / "Artist - Track.wav"
        link = library / "WAV" / "Linked - Track.wav"
        if not hasattr(Path, "symlink_to"):
            self.skipTest("symlinks unsupported")
        try:
            link.symlink_to(real)
        except OSError:
            self.skipTest("symlink creation failed")
        loc = encode_location(link)
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Track" Artist="Linked" Kind="WAV File"
           Location="{loc}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        _write_import_xml(library / "rekordbox-import.xml", xml)
        _write_manifest(
            library,
            {"/source/a.flac": {"wav": {"dest": "WAV/Linked - Track.wav"}}},
        )
        with self.assertRaises(CliError) as ctx:
            import_edit.load_import_edit_draft(library)
        self.assertIn("symlink", str(ctx.exception).casefold())


class RemoveTrackFromPlaylistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _library_with_shared_track(self) -> Path:
        """One collection track referenced by two playlists."""
        library = self.root / "lib"
        (library / "WAV").mkdir(parents=True)
        wav = library / "WAV" / "Artist - Track.wav"
        wav.write_bytes(b"RIFF")
        loc = encode_location(wav)
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Track" Artist="Artist" Kind="WAV File"
           Location="{loc}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Alpha [WAV]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
      <NODE Name="Beta [WAV]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        _write_import_xml(library / "rekordbox-import.xml", xml)
        _write_manifest(
            library,
            {"/source/a.flac": {"wav": {"dest": "WAV/Artist - Track.wav"}}},
        )
        return library

    def test_remove_track_from_playlist_preserves_collection_and_other_playlist(
        self,
    ) -> None:
        """Given a shared TrackID: When remove from Alpha only: Then Beta,
        COLLECTION, and manifest stay; Alpha Entries becomes 0."""
        library = self._library_with_shared_track()
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_track_from_playlist(
            draft, folder="", name="Alpha [WAV]", track_id="1"
        )
        self.assertTrue(draft.dirty)
        alpha = None
        beta = None
        for kind, folder, name, node in iter_playlist_nodes(draft.root):
            if name == "Alpha [WAV]":
                alpha = node
            if name == "Beta [WAV]":
                beta = node
        self.assertIsNotNone(alpha)
        self.assertIsNotNone(beta)
        self.assertEqual(alpha.findall("TRACK"), [])
        self.assertEqual(alpha.get("Entries"), "0")
        self.assertEqual([e.get("Key") for e in beta.findall("TRACK")], ["1"])
        collection = draft.root.find("COLLECTION")
        self.assertEqual(len(collection.findall("TRACK")), 1)
        self.assertIn("/source/a.flac", draft.manifest.tracks)
        self.assertEqual(draft.trash_relative_dests, set())
        self.assertTrue((library / "WAV" / "Artist - Track.wav").is_file())


class RemovePlaylistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _nested_library(self) -> Path:
        """Folder / Night Set [WAV] with one owned track; empty sibling folder."""
        library = self.root / "lib"
        (library / "WAV").mkdir(parents=True)
        wav = library / "WAV" / "Artist - Track.wav"
        wav.write_bytes(b"RIFF")
        loc = encode_location(wav)
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Track" Artist="Artist" Kind="WAV File"
           Location="{loc}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Shows" Type="0" Count="1">
        <NODE Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1">
          <TRACK Key="1"/>
        </NODE>
      </NODE>
      <NODE Name="Empty Folder" Type="0" Count="0"/>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        _write_import_xml(library / "rekordbox-import.xml", xml)
        _write_manifest(
            library,
            {"/source/a.flac": {"wav": {"dest": "WAV/Artist - Track.wav"}}},
        )
        return library

    def test_remove_playlist_orphans_collection_and_schedules_trash(self) -> None:
        """Given sole playlist ref: When remove playlist: Then collection and
        WAV assignment go away, AIFF sibling kept, file scheduled, folder kept."""
        library = self._nested_library()
        # Add independent AIFF assignment for same source (no XML row).
        draft = import_edit.load_import_edit_draft(library)
        draft.manifest.set_dest(
            "/source/a.flac", "aiff", "AIFF/Artist - Track.aiff"
        )
        impact = import_edit.remove_playlist(
            draft, folder="Shows", name="Night Set [WAV]"
        )
        self.assertTrue(draft.dirty)
        self.assertEqual(impact.playlist_entries_removed, 1)
        self.assertEqual(impact.collection_removed, 1)
        self.assertEqual(impact.files_to_trash, ["WAV/Artist - Track.wav"])
        self.assertEqual(impact.missing_files_cleaned, 0)
        playlists = [
            (folder, name)
            for kind, folder, name, _ in iter_playlist_nodes(draft.root)
            if kind == "playlist"
        ]
        self.assertEqual(playlists, [])
        folders = [
            name
            for kind, folder, name, _ in iter_playlist_nodes(draft.root)
            if kind == "folder"
        ]
        self.assertIn("Shows", folders)
        self.assertIn("Empty Folder", folders)
        self.assertEqual(len(draft.root.find("COLLECTION").findall("TRACK")), 0)
        self.assertNotIn("wav", draft.manifest.tracks["/source/a.flac"])
        self.assertIn("aiff", draft.manifest.tracks["/source/a.flac"])
        self.assertIn("WAV/Artist - Track.wav", draft.trash_relative_dests)

    def test_remove_playlist_with_repeated_keys_orphans_once(self) -> None:
        """Given a playlist that lists the same Key twice and nowhere else:
        When remove_playlist: Then collection_removed is 1 and trash lists
        the dest once; playlist_entries_removed stays 2."""
        library = _valid_library(self.root)
        xml_path = library / "rekordbox-import.xml"
        text = xml_path.read_text(encoding="utf-8")
        text = text.replace(
            '<TRACK Key="1"/>',
            '<TRACK Key="1"/><TRACK Key="1"/>',
        )
        text = text.replace(
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1"',
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="2"',
        )
        xml_path.write_text(text, encoding="utf-8")
        draft = import_edit.load_import_edit_draft(library)
        impact = import_edit.remove_playlist(
            draft, folder="", name="Night Set [WAV]"
        )
        self.assertEqual(impact.playlist_entries_removed, 2)
        self.assertEqual(impact.collection_removed, 1)
        self.assertEqual(impact.files_to_trash, ["WAV/Artist - Track.wav"])

    def test_remove_playlist_preserves_shared_collection_track(self) -> None:
        library = self.root / "lib"
        (library / "WAV").mkdir(parents=True)
        wav = library / "WAV" / "Artist - Track.wav"
        wav.write_bytes(b"RIFF")
        loc = encode_location(wav)
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Track" Artist="Artist" Kind="WAV File"
           Location="{loc}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Folder A" Type="0" Count="1">
        <NODE Name="Dup [WAV]" Type="1" KeyType="0" Entries="1">
          <TRACK Key="1"/>
        </NODE>
      </NODE>
      <NODE Name="Folder B" Type="0" Count="1">
        <NODE Name="Dup [WAV]" Type="1" KeyType="0" Entries="1">
          <TRACK Key="1"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        _write_import_xml(library / "rekordbox-import.xml", xml)
        _write_manifest(
            library,
            {"/source/a.flac": {"wav": {"dest": "WAV/Artist - Track.wav"}}},
        )
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_playlist(draft, folder="Folder A", name="Dup [WAV]")
        remaining = [
            (folder, name)
            for kind, folder, name, _ in iter_playlist_nodes(draft.root)
            if kind == "playlist"
        ]
        self.assertEqual(remaining, [("Folder B", "Dup [WAV]")])
        self.assertEqual(len(draft.root.find("COLLECTION").findall("TRACK")), 1)
        self.assertIn("wav", draft.manifest.tracks["/source/a.flac"])
        self.assertEqual(draft.trash_relative_dests, set())

    def test_remove_playlist_skips_manifest_for_dangling_key(self) -> None:
        library = _valid_library(self.root)
        xml_path = library / "rekordbox-import.xml"
        text = xml_path.read_text(encoding="utf-8")
        text = text.replace(
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1"',
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="2"',
        )
        text = text.replace(
            '<TRACK Key="1"/>',
            '<TRACK Key="1"/><TRACK Key="999"/>',
        )
        xml_path.write_text(text, encoding="utf-8")
        draft = import_edit.load_import_edit_draft(library)
        impact = import_edit.remove_playlist(
            draft, folder="", name="Night Set [WAV]"
        )
        self.assertEqual(impact.playlist_entries_removed, 2)
        self.assertEqual(impact.collection_removed, 1)
        self.assertEqual(len(draft.trash_relative_dests), 1)


class RemoveFromCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_remove_from_collection_clears_all_playlist_refs(self) -> None:
        library = self.root / "lib"
        (library / "WAV").mkdir(parents=True)
        wav = library / "WAV" / "Artist - Track.wav"
        wav.write_bytes(b"RIFF")
        loc = encode_location(wav)
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Track" Artist="Artist" Kind="WAV File"
           Location="{loc}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Alpha [WAV]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
      <NODE Name="Beta [WAV]" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        _write_import_xml(library / "rekordbox-import.xml", xml)
        _write_manifest(
            library,
            {
                "/source/a.flac": {
                    "wav": {"dest": "WAV/Artist - Track.wav"},
                    "aiff": {"dest": "AIFF/Artist - Track.aiff"},
                }
            },
        )
        draft = import_edit.load_import_edit_draft(library)
        impact = import_edit.remove_track_from_collection(draft, track_id="1")
        self.assertEqual(impact.playlist_refs_removed, 2)
        self.assertEqual(impact.collection_removed, 1)
        self.assertEqual(impact.files_to_trash, ["WAV/Artist - Track.wav"])
        for kind, _f, _n, node in iter_playlist_nodes(draft.root):
            if kind == "playlist":
                self.assertEqual(node.findall("TRACK"), [])
        self.assertEqual(len(draft.root.find("COLLECTION").findall("TRACK")), 0)
        self.assertNotIn("wav", draft.manifest.tracks.get("/source/a.flac", {}))
        self.assertIn("aiff", draft.manifest.tracks["/source/a.flac"])

    def test_remove_from_collection_allows_missing_file(self) -> None:
        library = _valid_library(self.root)
        (library / "WAV" / "Artist - Track.wav").unlink()
        draft = import_edit.load_import_edit_draft(library)
        impact = import_edit.remove_track_from_collection(draft, track_id="1")
        self.assertEqual(impact.missing_files_cleaned, 1)
        self.assertEqual(impact.files_to_trash, [])
        self.assertEqual(draft.trash_relative_dests, set())
        self.assertNotIn("/source/a.flac", draft.manifest.tracks)


class SaveImportEditDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_persists_xml_manifest_and_trashes_staged_files(self) -> None:
        library = _valid_library(self.root)
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_playlist(draft, folder="", name="Night Set [WAV]")
        trashed: list[Path] = []

        def fake_trash(path: Path) -> None:
            trashed.append(path)
            # Simulate Finder: remove the staging directory.
            import shutil

            shutil.rmtree(path)

        import_edit.save_import_edit_draft(draft, move_to_trash=fake_trash)
        self.assertFalse((library / "WAV" / "Artist - Track.wav").exists())
        self.assertEqual(len(trashed), 1)
        reloaded = import_edit.load_import_edit_draft(library)
        self.assertEqual(len(reloaded.root.find("COLLECTION").findall("TRACK")), 0)
        self.assertNotIn("/source/a.flac", reloaded.manifest.tracks)
        # Staging dir must not remain.
        staging = [
            p for p in library.iterdir() if p.name.startswith("import-edit-trash-")
        ]
        self.assertEqual(staging, [])

    def test_save_refuses_changed_fingerprint(self) -> None:
        library = _valid_library(self.root)
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_track_from_playlist(
            draft, folder="", name="Night Set [WAV]", track_id="1"
        )
        # External change to XML on disk.
        xml_path = library / "rekordbox-import.xml"
        xml_path.write_text(xml_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(CliError) as ctx:
            import_edit.save_import_edit_draft(
                draft, move_to_trash=lambda _p: None
            )
        self.assertIn("changed", str(ctx.exception).casefold())
        # Original audio still present.
        self.assertTrue((library / "WAV" / "Artist - Track.wav").is_file())

    def test_save_rolls_back_when_trash_fails_and_staging_remains(self) -> None:
        library = _valid_library(self.root)
        original_xml = (library / "rekordbox-import.xml").read_bytes()
        original_manifest = (library / cm.MANIFEST_NAME).read_bytes()
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_playlist(draft, folder="", name="Night Set [WAV]")

        def fail_trash(_path: Path) -> None:
            raise CliError("Trash failed")

        with self.assertRaises(CliError) as ctx:
            import_edit.save_import_edit_draft(draft, move_to_trash=fail_trash)
        self.assertIn("Trash", str(ctx.exception))
        self.assertEqual(
            (library / "rekordbox-import.xml").read_bytes(), original_xml
        )
        self.assertEqual(
            (library / cm.MANIFEST_NAME).read_bytes(), original_manifest
        )
        self.assertTrue((library / "WAV" / "Artist - Track.wav").is_file())
        self.assertTrue(draft.dirty)

    def test_save_ambiguous_trash_does_not_claim_success(self) -> None:
        library = _valid_library(self.root)
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_playlist(draft, folder="", name="Night Set [WAV]")

        def ambiguous_trash(path: Path) -> None:
            import shutil

            shutil.rmtree(path)
            raise RuntimeError("osascript timeout")

        with self.assertRaises(CliError) as ctx:
            import_edit.save_import_edit_draft(
                draft, move_to_trash=ambiguous_trash
            )
        msg = str(ctx.exception).casefold()
        self.assertRegex(msg, r"could not be proven|ambiguous|unknown")
        self.assertTrue(draft.dirty)
        # File is gone (already trashed/removed); do not claim rollback.
        self.assertFalse((library / "WAV" / "Artist - Track.wav").exists())


class CollectionTrackLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collection_track_label_uses_artist_and_name(self) -> None:
        """Given a collection TRACK: When labeling: Then artist - name, not TrackID."""
        library = _valid_library(self.root)
        draft = import_edit.load_import_edit_draft(library)
        self.assertEqual(
            import_edit.collection_track_label(draft.root, "1"),
            "Artist - Track",
        )
        self.assertNotIn("TrackID", import_edit.collection_track_label(draft.root, "1"))


class EditPreviewRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_preview_save_after_playlist_remove_lists_remove_action(self) -> None:
        """Given a draft after removing a playlist ref: When previewing Save:
        Then the table lists the track as Remove from playlist."""
        library = _valid_library(self.root)
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_track_from_playlist(
            draft, folder="", name="Night Set [WAV]", track_id="1"
        )
        rows = import_edit.preview_save(draft)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].track, "Artist - Track")
        self.assertEqual(rows[0].action, import_edit.ACTION_REMOVE_FROM_PLAYLIST)
        self.assertEqual(rows[0].playlist, "Night Set [WAV]")

    def test_preview_save_removing_one_repeated_key_lists_one_row(self) -> None:
        """Given a playlist that lists the same track twice: When one Key is
        removed: Then preview lists one Remove from playlist row."""
        library = _valid_library(self.root)
        xml_path = library / "rekordbox-import.xml"
        text = xml_path.read_text(encoding="utf-8")
        text = text.replace(
            '<TRACK Key="1"/>',
            '<TRACK Key="1"/><TRACK Key="1"/>',
        )
        text = text.replace(
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="1"',
            'Name="Night Set [WAV]" Type="1" KeyType="0" Entries="2"',
        )
        xml_path.write_text(text, encoding="utf-8")
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_track_from_playlist(
            draft, folder="", name="Night Set [WAV]", track_id="1"
        )
        rows = import_edit.preview_save(draft)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].action, import_edit.ACTION_REMOVE_FROM_PLAYLIST)
        self.assertEqual(rows[0].playlist, "Night Set [WAV]")

    def test_preview_save_after_collection_remove_lists_trash_action(self) -> None:
        """Given a draft after collection removal: When previewing Save:
        Then the table lists Move to Trash with the track name."""
        library = _valid_library(self.root)
        draft = import_edit.load_import_edit_draft(library)
        import_edit.remove_track_from_collection(draft, track_id="1")
        rows = import_edit.preview_save(draft)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].track, "Artist - Track")
        self.assertEqual(rows[0].action, import_edit.ACTION_MOVE_TO_TRASH)
        self.assertEqual(rows[0].playlist, "Night Set [WAV]")

    def test_preview_save_unknown_collection_remove_lists_trash_action(self) -> None:
        """Given a collection-only track: When trashed: Then preview lists Unknown."""
        import copy

        from rekordbox_xml import UNKNOWN_PLAYLIST_NAME

        draft = import_edit.load_import_edit_draft(_valid_library(self.root))
        import_edit.remove_track_from_playlist(
            draft, folder="", name="Night Set [WAV]", track_id="1"
        )
        draft.original_root = copy.deepcopy(draft.root)
        import_edit.remove_track_from_collection(draft, track_id="1")
        rows = import_edit.preview_save(draft)
        self.assertEqual(
            [(r.track, r.action, r.playlist) for r in rows],
            [("Artist - Track", import_edit.ACTION_MOVE_TO_TRASH, UNKNOWN_PLAYLIST_NAME)],
        )


class MovePathToTrashTests(unittest.TestCase):
    def test_move_path_to_trash_uses_nsfilemanager_javascript(self) -> None:
        """Given a hidden staging dir: When trashing: Then JXA NSFileManager
        receives the path as an argument (Finder cannot see dotfiles)."""
        from unittest.mock import patch
        import subprocess as sp

        completed = sp.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        target = Path("/tmp/My Library/import-edit-trash-abc")
        with patch("import_edit.sys.platform", "darwin"), patch(
            "import_edit.subprocess.run", return_value=completed
        ) as run:
            import_edit.move_path_to_trash(target)
        args = run.call_args[0][0]
        self.assertEqual(args[0], "osascript")
        self.assertIn("-l", args)
        self.assertIn("JavaScript", args)
        self.assertIn(str(target.resolve()), args)
        joined = " ".join(args)
        self.assertIn("trashItemAtURL", joined)
        self.assertNotIn("POSIX file '", joined)


if __name__ == "__main__":
    unittest.main()
