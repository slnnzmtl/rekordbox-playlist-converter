#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from update_check import UpdateCheckResult

NESTED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="0"/>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Top" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
      <NODE Name="Intelligent playlists" Type="0" Count="2">
        <NODE Name="Nested" Type="1" KeyType="0" Entries="2">
          <TRACK Key="1"/><TRACK Key="2"/>
        </NODE>
        <NODE Name="Sibling" Type="1" KeyType="0" Entries="1">
          <TRACK Key="3"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""

DUP_NAME_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="0"/>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="One" Type="0" Count="1">
        <NODE Name="Same" Type="1" KeyType="0" Entries="1">
          <TRACK Key="1"/>
        </NODE>
      </NODE>
      <NODE Name="Two" Type="0" Count="1">
        <NODE Name="Same" Type="1" KeyType="0" Entries="1">
          <TRACK Key="2"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""

TRACKLIST_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="1" Name="Bestial" Artist="ABSL"
           Location="file://localhost/Users/me/music/Bestial.flac" Kind="FLAC File"/>
    <TRACK TrackID="2" Name="Revelation" Artist="Shogan"
           Location="file://localhost/Users/me/music/Revelation.aiff" Kind="AIFF File"/>
    <TRACK TrackID="3" Name="NoLoc" Artist="Ghost" Kind="WAV File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Dark forest" Type="1" KeyType="0" Entries="3">
        <TRACK Key="1"/>
        <TRACK Key="2"/>
        <TRACK Key="999"/>
      </NODE>
      <NODE Name="Morning" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/>
        <TRACK Key="3"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


def _tk_available() -> bool:
    try:
        import _tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def _write_xml(directory: Path, text: str) -> Path:
    path = directory / "rekordbox.xml"
    path.write_text(text, encoding="utf-8")
    return path


class GuiPlaylistExplorerTests(unittest.TestCase):
    def _make_app(self, source: Path):
        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        with patch(
            "rb_converter_gui.check_for_update",
            return_value=UpdateCheckResult(kind="up_to_date"),
        ), patch(
            "rb_converter_gui.load_preferences",
            return_value={"source_xml": str(source)},
        ), patch(
            "rb_converter_gui.resolve_startup_paths",
            return_value=(DEFAULT_WAV_DIR, DEFAULT_OUTPUT),
        ), patch("rb_converter_gui.rb.discover_xml_candidates", return_value=[]):
            root = tk.Tk()
            root.withdraw()
            app = ConverterApp(root, documents_accessible=False)
        return root, app

    def test_load_playlists_paints_folder_tree(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = _write_xml(Path(tmp), NESTED_XML)
                root, app = self._make_app(source)

                tree = app.playlist_tree
                top = tree.get_children("")
                self.assertEqual(len(top), 2)
                self.assertEqual(tree.item(top[0], "text"), "Top (1 tracks)")
                self.assertEqual(tree.item(top[1], "text"), "Intelligent playlists")
                children = tree.get_children(top[1])
                self.assertEqual(len(children), 2)
                self.assertEqual(tree.item(children[0], "text"), "Nested (2 tracks)")
                self.assertEqual(tree.item(children[1], "text"), "Sibling (1 tracks)")
                # Folders start collapsed.
                self.assertFalse(tree.item(top[1], "open"))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_selected_playlists_expands_folder_to_descendants(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = _write_xml(Path(tmp), NESTED_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                folder_iid = tree.get_children("")[1]
                tree.selection_set(folder_iid)
                self.assertEqual(
                    app._selected_playlists(),
                    [
                        ("Intelligent playlists", "Nested"),
                        ("Intelligent playlists", "Sibling"),
                    ],
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_selected_playlists_dedupes_folder_and_child(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = _write_xml(Path(tmp), NESTED_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                folder_iid = tree.get_children("")[1]
                nested_iid = tree.get_children(folder_iid)[0]
                tree.selection_set(folder_iid, nested_iid)
                self.assertEqual(
                    app._selected_playlists(),
                    [
                        ("Intelligent playlists", "Nested"),
                        ("Intelligent playlists", "Sibling"),
                    ],
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_selected_playlists_rejects_same_leaf_name(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import rb_playlist_to_wav as rb

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = _write_xml(Path(tmp), DUP_NAME_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                one = tree.get_children("")[0]
                two = tree.get_children("")[1]
                tree.selection_set(tree.get_children(one)[0], tree.get_children(two)[0])
                tree.event_generate("<<TreeviewSelect>>")
                preview = app.tracklist_tree
                groups = preview.get_children("")
                self.assertEqual(
                    [preview.item(g, "text") for g in groups],
                    ["Same (1 tracks)", "Same (1 tracks)"],
                )
                with self.assertRaises(rb.CliError) as ctx:
                    app._selected_playlists()
                self.assertIn("same name", str(ctx.exception))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_search_keeps_ancestors_and_folder_selects_visible_only(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = _write_xml(Path(tmp), NESTED_XML)
                root, app = self._make_app(source)
                app._search_showing_placeholder = False
                app.search_var.set("nested")
                tree = app.playlist_tree
                top = tree.get_children("")
                self.assertEqual(len(top), 1)
                self.assertEqual(tree.item(top[0], "text"), "Intelligent playlists")
                self.assertTrue(tree.item(top[0], "open"))
                children = tree.get_children(top[0])
                self.assertEqual(len(children), 1)
                self.assertEqual(tree.item(children[0], "text"), "Nested (2 tracks)")
                tree.selection_set(top[0])
                self.assertEqual(
                    app._selected_playlists(),
                    [("Intelligent playlists", "Nested")],
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_preview_shows_rows_unique_total_and_empty(self) -> None:
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = _write_xml(Path(tmp), TRACKLIST_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                dark, morning = tree.get_children("")
                tree.selection_set(dark)
                tree.event_generate("<<TreeviewSelect>>")

                preview = app.tracklist_tree
                groups = preview.get_children("")
                self.assertEqual(preview.item(groups[0], "text"), "Dark forest (3 tracks)")
                self.assertEqual(
                    [preview.item(r, "text") for r in preview.get_children(groups[0])],
                    [
                        "ABSL - Bestial.flac",
                        "Shogan - Revelation.aiff",
                        "(missing track)",
                    ],
                )
                self.assertEqual(
                    app.status_var.get(),
                    "3 unique tracks from 1 playlist",
                )

                tree.selection_set(dark, morning)
                tree.event_generate("<<TreeviewSelect>>")
                self.assertEqual(
                    [preview.item(g, "text") for g in preview.get_children("")],
                    ["Dark forest (3 tracks)", "Morning (2 tracks)"],
                )
                self.assertEqual(
                    app.status_var.get(),
                    "4 unique tracks from 2 playlists",
                )

                tree.selection_set()
                tree.event_generate("<<TreeviewSelect>>")
                self.assertEqual(preview.get_children(""), ())
                self.assertEqual(
                    app.status_var.get(),
                    "Loaded 2 playlist(s). Select and Convert.",
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
