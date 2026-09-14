#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import converter_manifest as cm
from gui_tk import (
    app_patches,
    mark_output_folder_valid,
    merge_patches,
    run_inline_thread,
    startup_patches,
    tk_available,
)
from rekordbox_xml import encode_location


def _patches(wav_dir=None, **extra):
    return merge_patches(
        startup_patches(wav_dir=wav_dir) if wav_dir is not None else startup_patches(),
        {
            "threading.Thread": {"side_effect": run_inline_thread},
            "show_centered_message": None,
            "ask_centered_yesno": {"return_value": True},
        },
        extra,
    )


def _seed_library(tmp: Path) -> Path:
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
    (library / "rekordbox-import.xml").write_text(xml, encoding="utf-8")
    (library / cm.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "layout": "format-flat",
                "tracks": {
                    "/source/a.flac": {"wav": {"dest": "WAV/Artist - Track.wav"}}
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return library


def _seed_two_track_library(tmp: Path) -> Path:
    library = tmp / "lib2"
    (library / "WAV").mkdir(parents=True)
    wav1 = library / "WAV" / "Artist - One.wav"
    wav2 = library / "WAV" / "Artist - Two.wav"
    wav1.write_bytes(b"RIFF")
    wav2.write_bytes(b"RIFF")
    loc1 = encode_location(wav1)
    loc2 = encode_location(wav2)
    xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="One" Artist="Artist" Kind="WAV File"
           Location="{loc1}" SampleRate="44100"/>
    <TRACK TrackID="2" Name="Two" Artist="Artist" Kind="WAV File"
           Location="{loc2}" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Night Set [WAV]" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/>
        <TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
    (library / "rekordbox-import.xml").write_text(xml, encoding="utf-8")
    (library / cm.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "layout": "format-flat",
                "tracks": {
                    "/source/a.flac": {"wav": {"dest": "WAV/Artist - One.wav"}},
                    "/source/b.flac": {"wav": {"dest": "WAV/Artist - Two.wav"}},
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return library


SOURCE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="10" Name="Source" Artist="DJ" Kind="FLAC File"
           Location="file://localhost/Users/me/music/Source.flac"
           SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Source Playlist" Type="1" KeyType="0" Entries="1">
        <TRACK Key="10"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


class GuiImportEditModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_edit_button_shown_when_import_xml_and_manifest_exist(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._update_import_edit_button()
                self.assertEqual(str(app.import_edit_btn.cget("state")), "normal")
                app.import_edit_btn.pack(side=tk.LEFT)
                self.assertTrue(str(app.import_edit_btn.winfo_manager()))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_edit_mode_preserves_source_and_locks_convert(self) -> None:
        """Given source XML loaded: When enter edit mode: Then source root stays,
        view shows import playlists, Convert is hidden, Save disabled until dirty."""
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        source = self.root_dir / "source.xml"
        source.write_text(SOURCE_XML, encoding="utf-8")
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app.xml_var.set(str(source))
                app._load_playlists()
                source_root = app._source_root
                self.assertIsNotNone(source_root)
                app._enter_import_edit_mode()
                self.assertIs(app._source_root, source_root)
                self.assertTrue(app._import_edit_active())
                self.assertIs(app._view_root, app._import_edit_draft.root)
                names = [
                    e.name
                    for e in app._playlist_entries
                    if e.kind.value == "playlist"
                ]
                self.assertEqual(names, ["Night Set [WAV]"])
                self.assertEqual(app.convert_btn.winfo_manager(), "")
                self.assertEqual(str(app.import_save_btn.cget("state")), "disabled")
                self.assertEqual(str(app.xml_browse_btn.cget("state")), "disabled")
                # Dirty then Save enables.
                app._import_edit_draft.mark_dirty()
                app._sync_import_edit_chrome()
                self.assertEqual(str(app.import_save_btn.cget("state")), "normal")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_cancel_restores_source_view(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        source = self.root_dir / "source.xml"
        source.write_text(SOURCE_XML, encoding="utf-8")
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app.xml_var.set(str(source))
                app._load_playlists()
                app._enter_import_edit_mode()
                app._leave_import_edit_mode(discard=True)
                names = [
                    e.name
                    for e in app._playlist_entries
                    if e.kind.value == "playlist"
                ]
                self.assertEqual(names, ["Source Playlist"])
                self.assertFalse(app._import_edit_active())
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_start_convert_blocked_while_editing(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        source = self.root_dir / "source.xml"
        source.write_text(SOURCE_XML, encoding="utf-8")
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)) as mocks:
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app.xml_var.set(str(source))
                app._load_playlists()
                app._enter_import_edit_mode()
                app._start_convert()
                mocks["show_centered_message"].assert_called()
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_enter_edit_shows_error_when_load_fails(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = self.root_dir / "empty"
        library.mkdir()
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)) as mocks:
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                # Force button path even without files.
                app._enter_import_edit_mode()
                mocks["show_centered_message"].assert_called()
                self.assertFalse(app._import_edit_active())
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()


class GuiImportEditMenusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_track_prefixed_in_edit_mode(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        # Add dangling key.
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
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._enter_import_edit_mode()
                # Select playlist and refresh tracklist.
                for iid, meta in app._playlist_iids.items():
                    if meta[0] == "playlist":
                        app.playlist_tree.selection_set(iid)
                        break
                app._refresh_tracklist_preview()
                labels = [
                    app.tracklist_tree.item(iid, "values")[0]
                    for iid in app._tracklist_iids
                ]
                self.assertTrue(any(lab.startswith("! ") for lab in labels))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_save_confirms_pending_edits_not_immediate_remove(self) -> None:
        """Given edit mode: When removing a track: Then no confirm dialog.
        When Save: Then the preview lists the track and Trash action."""
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from unittest.mock import patch
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        tk_root = None
        captured: list = []
        try:
            with app_patches(**_patches(wav_dir=library)) as mocks, patch.object(
                ConverterApp,
                "_confirm_edit_preview",
                side_effect=lambda **kw: captured.append(kw) or True,
            ), patch(
                "gui.runtime.save_import_edit_draft"
            ):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._enter_import_edit_mode()
                app._edit_remove_track_from_collection("1")
                self.assertEqual(captured, [])
                app._save_import_edit()
                self.assertTrue(captured)
                rows = captured[0]["rows"]
                self.assertEqual(rows[0].track, "Artist - Track")
                self.assertEqual(rows[0].action, "Move to Trash")
                mocks["show_centered_message"].assert_called()
                info_kw = mocks["show_centered_message"].call_args
                self.assertEqual(info_kw.kwargs.get("title") or info_kw[0][1], "Saved")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_remove_track_from_playlist_keeps_playlist_selected(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from unittest.mock import patch
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._enter_import_edit_mode()
                for iid, meta in app._playlist_iids.items():
                    if meta[0] == "playlist":
                        app.playlist_tree.selection_set(iid)
                        break
                app._edit_remove_track_from_playlist(
                    "", "Night Set [WAV]", "1"
                )
                app._apply_playlist_filter()
                selected = app._selected_playlists(unique_names=False)
                self.assertEqual(selected, [("", "Night Set [WAV]")])
                names = [
                    e.name
                    for e in app._playlist_entries
                    if e.kind.value == "playlist"
                ]
                self.assertIn("Night Set [WAV]", names)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_remove_last_playlist_ref_lists_track_under_unknown(self) -> None:
        """Given the only playlist Key is removed: When the tree rebuilds:
        Then Unknown lists the collection track."""
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._enter_import_edit_mode()
                app._edit_remove_track_from_playlist(
                    "", "Night Set [WAV]", "1"
                )
                names = [
                    e.name
                    for e in app._playlist_entries
                    if e.kind.value == "playlist"
                ]
                self.assertIn("Unknown", names)
                unknown_iid = None
                for iid, meta in app._playlist_iids.items():
                    if meta[0] == "playlist" and meta[2] == "Unknown":
                        unknown_iid = iid
                        break
                self.assertIsNotNone(unknown_iid)
                app.playlist_tree.selection_set(unknown_iid)
                app._refresh_tracklist_preview()
                labels = [
                    app.tracklist_tree.item(iid, "values")[0]
                    for iid in app._tracklist_iids
                ]
                self.assertIn("Artist - Track", labels)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_remove_from_collection_keeps_playlist_selected(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from unittest.mock import patch
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._enter_import_edit_mode()
                for iid, meta in app._playlist_iids.items():
                    if meta[0] == "playlist":
                        app.playlist_tree.selection_set(iid)
                        break
                app._edit_remove_track_from_collection("1")
                app._apply_playlist_filter()
                selected = app._selected_playlists(unique_names=False)
                self.assertEqual(selected, [("", "Night Set [WAV]")])
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_remove_from_playlist_applies_to_all_selected_tracks(self) -> None:
        """Given two selected tracklist rows: When remove from playlist:
        Then both Keys leave that playlist and stay in the collection."""
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp
        from rekordbox_xml import iter_playlist_nodes

        library = _seed_two_track_library(self.root_dir)
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._enter_import_edit_mode()
                for iid, meta in app._playlist_iids.items():
                    if meta[0] == "playlist":
                        app.playlist_tree.selection_set(iid)
                        break
                app._refresh_tracklist_preview()
                leaves = list(app._tracklist_iids)
                self.assertEqual(len(leaves), 2)
                app.tracklist_tree.selection_set(*leaves)
                app._edit_remove_selected_tracks_from_playlist()
                draft = app._import_edit_draft
                keys = []
                for kind, _f, name, node in iter_playlist_nodes(draft.root):
                    if kind == "playlist" and name == "Night Set [WAV]":
                        keys = [e.get("Key") for e in node.findall("TRACK")]
                self.assertEqual(keys, [])
                collection = draft.root.find("COLLECTION")
                self.assertEqual(len(collection.findall("TRACK")), 2)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_remove_from_collection_applies_to_all_selected_tracks(self) -> None:
        """Given two selected tracklist rows: When remove from collection:
        Then both collection rows and playlist Keys are gone."""
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = _seed_two_track_library(self.root_dir)
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._enter_import_edit_mode()
                for iid, meta in app._playlist_iids.items():
                    if meta[0] == "playlist":
                        app.playlist_tree.selection_set(iid)
                        break
                app._refresh_tracklist_preview()
                leaves = list(app._tracklist_iids)
                app.tracklist_tree.selection_set(*leaves)
                app._edit_remove_selected_tracks_from_collection()
                draft = app._import_edit_draft
                collection = draft.root.find("COLLECTION")
                self.assertEqual(len(collection.findall("TRACK")), 0)
                self.assertEqual(
                    sorted(draft.trash_relative_dests),
                    ["WAV/Artist - One.wav", "WAV/Artist - Two.wav"],
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()

    def test_edit_button_packs_like_output_browse(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")
        import tkinter as tk
        from rb_converter_gui import ConverterApp

        library = _seed_library(self.root_dir)
        tk_root = None
        try:
            with app_patches(**_patches(wav_dir=library)):
                tk_root = tk.Tk()
                tk_root.withdraw()
                app = ConverterApp(tk_root, documents_accessible=False)
                mark_output_folder_valid(app)
                app._update_import_edit_button()
                tk_root.update_idletasks()
                self.assertEqual(
                    app.wav_dir_browse_btn.pack_info().get("side"), "left"
                )
                self.assertEqual(
                    app.import_edit_btn.pack_info().get("side"), "left"
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if tk_root is not None:
                tk_root.destroy()


if __name__ == "__main__":
    unittest.main()
