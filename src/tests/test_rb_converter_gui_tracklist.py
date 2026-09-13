#!/usr/bin/env python3
from __future__ import annotations

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

from test_preview_bit_depth import _flac_with_bit_depth

from gui_tk import app_patches, mark_output_folder_valid, run_inline_thread, startup_patches, tk_available
from gui_xml_fixtures import TRACKLIST_XML, write_xml
from rekordbox_xml import encode_location
from gui.tracklist import track_preview_row


def _flush_debounced(app, attr: str, callback) -> None:
    """Cancel a pending search after() and apply immediately."""
    after_id = getattr(app, attr)
    if after_id is not None:
        app.root.after_cancel(after_id)
        setattr(app, attr, None)
    callback()

def _flush_track_search_debounce(app) -> None:
    _flush_debounced(app, "_track_search_after_id", app._refresh_tracklist_preview)


class TrackPreviewLabelTests(unittest.TestCase):
    def test_preview_label_omits_location_suffix(self) -> None:
        """Given a TRACK with a .flac Location: When building the preview
        row: Then the label is artist - title without the extension."""
        track = {
            "Artist": "ABSL",
            "Name": "Bestial",
            "Location": "file://localhost/Users/me/music/Bestial.flac",
            "Kind": "FLAC File",
            "SampleRate": "44100",
        }
        label, fmt, _depth, rate = track_preview_row(track)
        self.assertEqual(label, "ABSL - Bestial")
        self.assertEqual(fmt, "FLAC")
        self.assertEqual(rate, "44100")


class GuiTracklistTests(unittest.TestCase):
    def _make_app(self, source: Path):
        import tkinter as tk
        from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR, ConverterApp

        with app_patches(
            **startup_patches(preferences={"source_xml": str(source)}),
        ):
            root = tk.Tk()
            root.withdraw()
            app = ConverterApp(root, documents_accessible=False)
        return root, app

    def test_tracklist_preview_shows_rows_unique_total_and_empty(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), TRACKLIST_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                dark, morning = tree.get_children("")
                tree.selection_set(dark)
                tree.event_generate("<<TreeviewSelect>>")

                preview = app.tracklist_tree
                groups = preview.get_children("")
                self.assertEqual(preview.item(groups[0], "text"), "Dark forest (3 tracks)")
                leaves = preview.get_children(groups[0])
                self.assertEqual(
                    [preview.item(r, "text") for r in leaves],
                    [
                        "ABSL - Bestial",
                        "Shogan - Revelation",
                        "(missing track)",
                    ],
                )
                self.assertEqual(
                    [preview.item(r, "values") for r in leaves],
                    [
                        ("FLAC", "—", "44100"),
                        ("AIFF", "—", "48000"),
                        ("—", "—", "—"),
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
                morning_leaves = preview.get_children(preview.get_children("")[1])
                self.assertEqual(
                    [preview.item(r, "values") for r in morning_leaves],
                    [
                        ("FLAC", "—", "44100"),
                        ("WAV", "—", "—"),
                    ],
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

    def test_tracklist_group_open_state_persists_across_selection(self) -> None:
        """Arrow click collapses without selecting; collapsed groups stay closed
        when another playlist is added; after full deselect, reselect expands."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from unittest.mock import patch

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), TRACKLIST_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                dark, morning = tree.get_children("")
                tree.selection_set(dark)
                tree.event_generate("<<TreeviewSelect>>")

                preview = app.tracklist_tree
                dark_group = preview.get_children("")[0]
                preview.selection_set()
                preview.event_generate("<<TreeviewSelect>>")
                self.assertTrue(preview.item(dark_group, "open"))
                with patch.object(
                    preview, "identify_row", return_value=dark_group
                ), patch.object(
                    preview, "identify", return_value="Treeitem.indicator"
                ):
                    result = app._on_tracklist_button1(
                        type("E", (), {"x": 1, "y": 1})()
                    )
                self.assertEqual(result, "break")
                self.assertFalse(preview.item(dark_group, "open"))
                self.assertEqual(preview.selection(), ())

                tree.selection_set(dark, morning)
                tree.event_generate("<<TreeviewSelect>>")
                groups = preview.get_children("")
                self.assertEqual(len(groups), 2)
                self.assertFalse(preview.item(groups[0], "open"))
                self.assertTrue(preview.item(groups[1], "open"))

                tree.selection_set(morning)
                tree.event_generate("<<TreeviewSelect>>")
                tree.selection_set(dark, morning)
                tree.event_generate("<<TreeviewSelect>>")
                groups = preview.get_children("")
                self.assertTrue(preview.item(groups[0], "open"))
                self.assertTrue(preview.item(groups[1], "open"))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_selection_follows_leaves_and_group_header(self) -> None:
        """Listed leaves start selected; header remaps to leaves; header tag
        tracks whether any of that group's tracks are selected."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), TRACKLIST_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                dark, morning = tree.get_children("")
                tree.selection_set(dark, morning)
                tree.event_generate("<<TreeviewSelect>>")

                preview = app.tracklist_tree
                groups = preview.get_children("")
                dark_leaves = list(preview.get_children(groups[0]))
                morning_leaves = list(preview.get_children(groups[1]))
                all_leaves = dark_leaves + morning_leaves
                self.assertEqual(set(preview.selection()), set(all_leaves))

                preview.selection_set(*[l for l in all_leaves if l != dark_leaves[1]])
                preview.event_generate("<<TreeviewSelect>>")
                self.assertEqual(
                    app.status_var.get(),
                    "3 unique tracks from 2 playlists",
                )

                preview.selection_set(groups[0])
                preview.event_generate("<<TreeviewSelect>>")
                self.assertEqual(set(preview.selection()), set(dark_leaves))
                self.assertEqual(
                    app.status_var.get(),
                    "3 unique tracks from 1 playlist",
                )
                self.assertIn(
                    "playlist_header_selected",
                    preview.item(groups[0], "tags"),
                )
                self.assertNotIn(
                    "playlist_header_selected",
                    preview.item(groups[1], "tags"),
                )

                preview.selection_set()
                preview.event_generate("<<TreeviewSelect>>")
                for group in groups:
                    self.assertNotIn(
                        "playlist_header_selected",
                        preview.item(group, "tags"),
                    )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_track_search_filters_listed_rows_and_clears(self) -> None:
        """Track search matches labels, format, and filename; clear restores."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), TRACKLIST_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                dark, morning = tree.get_children("")
                tree.selection_set(dark, morning)
                tree.event_generate("<<TreeviewSelect>>")

                preview = app.tracklist_tree
                app.track_search_var.set("revelation")
                _flush_track_search_debounce(app)
                groups = preview.get_children("")
                self.assertEqual(len(groups), 1)
                leaves = preview.get_children(groups[0])
                self.assertEqual(
                    [preview.item(r, "text") for r in leaves],
                    ["Shogan - Revelation"],
                )
                self.assertEqual(
                    app.status_var.get(),
                    "1 unique tracks from 1 playlist",
                )

                app.track_search_var.set("aiff")
                _flush_track_search_debounce(app)
                groups = preview.get_children("")
                self.assertEqual(len(groups), 1)
                leaves = preview.get_children(groups[0])
                self.assertEqual(
                    [preview.item(r, "text") for r in leaves],
                    ["Shogan - Revelation"],
                )

                app.track_search_var.set("")
                _flush_track_search_debounce(app)
                groups = preview.get_children("")
                self.assertEqual(len(groups), 2)
                all_leaves = list(preview.get_children(groups[0])) + list(
                    preview.get_children(groups[1])
                )
                self.assertEqual(set(preview.selection()), set(all_leaves))
                self.assertEqual(
                    app.status_var.get(),
                    "4 unique tracks from 2 playlists",
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_bit_depth_from_on_disk_file(self) -> None:
        """Bit depth paints as — then fills async; flush after(0) to apply."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from unittest.mock import patch

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                flac = base / "Bestial.flac"
                flac.write_bytes(_flac_with_bit_depth(24))
                xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Bestial" Artist="ABSL"
           Location="{encode_location(flac)}"
           Kind="FLAC File" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Crate" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
                source = write_xml(base, xml)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                crate = tree.get_children("")[0]
                with app_patches(
                    **{"threading.Thread": {"side_effect": run_inline_thread}}
                ):
                    tree.selection_set(crate)
                    tree.event_generate("<<TreeviewSelect>>")
                    preview = app.tracklist_tree
                    leaves = preview.get_children(preview.get_children("")[0])
                    self.assertEqual(
                        [preview.item(r, "values") for r in leaves],
                        [("FLAC", "—", "44100")],
                    )
                    self.assertEqual(
                        app.status_var.get(),
                        "1 unique tracks from 1 playlist",
                    )
                    self.assertEqual(
                        app.scan_status_var.get(),
                        "Scanning bit depth…",
                    )
                    root.update()
                    leaves = preview.get_children(preview.get_children("")[0])
                    self.assertEqual(
                        [preview.item(r, "values") for r in leaves],
                        [("FLAC", "24", "44100")],
                    )
                    self.assertEqual(app.scan_status_var.get(), "")
                    tree.selection_set(crate)
                    tree.event_generate("<<TreeviewSelect>>")
                    self.assertEqual(app.scan_status_var.get(), "")
                    self.assertEqual(
                        app.status_var.get(),
                        "1 unique tracks from 1 playlist",
                    )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_bit_depth_cache_clears_on_xml_refresh(self) -> None:
        """XML reload drops the session cache; first paint is — again."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from unittest.mock import patch

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                flac = base / "Bestial.flac"
                flac.write_bytes(_flac_with_bit_depth(24))
                xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Bestial" Artist="ABSL"
           Location="{encode_location(flac)}"
           Kind="FLAC File" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Crate" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
                source = write_xml(base, xml)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                with app_patches(
                    **{"threading.Thread": {"side_effect": run_inline_thread}}
                ):
                    crate = tree.get_children("")[0]
                    tree.selection_set(crate)
                    tree.event_generate("<<TreeviewSelect>>")
                    root.update()
                    app._refresh_xml()
                    crate = tree.get_children("")[0]
                    tree.selection_set(crate)
                    tree.event_generate("<<TreeviewSelect>>")
                    preview = app.tracklist_tree
                    leaves = preview.get_children(preview.get_children("")[0])
                    self.assertEqual(
                        [preview.item(r, "values") for r in leaves],
                        [("FLAC", "—", "44100")],
                    )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_hides_unsupported_formats_keeps_missing(self) -> None:
        """Path rows outside SUPPORTED_LOSSLESS_EXT are hidden; missing stays."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from unittest.mock import patch

        MIXED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="1" Name="Bestial" Artist="ABSL"
           Location="file://localhost/Users/me/music/Bestial.flac"
           Kind="FLAC File" SampleRate="44100"/>
    <TRACK TrackID="2" Name="Lossy" Artist="Skip"
           Location="file://localhost/Users/me/music/Lossy.mp3"
           Kind="MP3 File" SampleRate="44100"/>
    <TRACK TrackID="3" Name="Aac" Artist="Skip"
           Location="file://localhost/Users/me/music/Aac.aac"
           Kind="AAC File" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Mixed" Type="1" KeyType="0" Entries="4">
        <TRACK Key="1"/>
        <TRACK Key="2"/>
        <TRACK Key="3"/>
        <TRACK Key="999"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), MIXED_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                mixed = tree.get_children("")[0]
                tree.selection_set(mixed)
                tree.event_generate("<<TreeviewSelect>>")

                preview = app.tracklist_tree
                groups = preview.get_children("")
                self.assertEqual(len(groups), 1)
                self.assertEqual(preview.item(groups[0], "text"), "Mixed (2 tracks)")
                leaves = preview.get_children(groups[0])
                self.assertEqual(
                    [preview.item(r, "text") for r in leaves],
                    ["ABSL - Bestial", "(missing track)"],
                )

                preview.selection_set(*leaves)
                preview.event_generate("<<TreeviewSelect>>")

                prepare_calls: list[tuple] = []

                def fake_prepare(*args, **kwargs):
                    prepare_calls.append((args, kwargs))
                    return (None, ["stop"])

                with app_patches(
                    {
                        "prepare_batch": {"side_effect": fake_prepare},
                        "threading.Thread": {"side_effect": run_inline_thread},
                        "show_centered_message": None,
                    }
                ):
                    mark_output_folder_valid(app)
                    app._start_convert()
                    root.update()

                self.assertEqual(len(prepare_calls), 1)
                _args, kwargs = prepare_calls[0]
                keys = set(
                    kwargs.get("track_keys_by_playlist", {}).get(("", "Mixed"), [])
                )
                self.assertIn("1", keys)
                self.assertNotIn("2", keys)
                self.assertNotIn("3", keys)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_heading_sorts_within_groups(self) -> None:
        """Heading clicks sort leaves inside each group; reverse; survives search."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        SORT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="4">
    <TRACK TrackID="1" Name="Zebra" Artist="Z"
           Location="file://localhost/Users/me/music/Zebra.flac"
           Kind="FLAC File" SampleRate="48000"/>
    <TRACK TrackID="2" Name="Alpha" Artist="A"
           Location="file://localhost/Users/me/music/Alpha.aiff"
           Kind="AIFF File" SampleRate="44100"/>
    <TRACK TrackID="3" Name="Mid" Artist="M"
           Location="file://localhost/Users/me/music/Mid.wav"
           Kind="WAV File" SampleRate="48000"/>
    <TRACK TrackID="4" Name="Only" Artist="O"
           Location="file://localhost/Users/me/music/Only.flac"
           Kind="FLAC File" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Crate" Type="1" KeyType="0" Entries="3">
        <TRACK Key="1"/>
        <TRACK Key="2"/>
        <TRACK Key="3"/>
      </NODE>
      <NODE Name="Solo" Type="1" KeyType="0" Entries="1">
        <TRACK Key="4"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), SORT_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                crate, solo = tree.get_children("")
                tree.selection_set(crate, solo)
                tree.event_generate("<<TreeviewSelect>>")
                preview = app.tracklist_tree
                groups = preview.get_children("")
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "text") for r in crate_leaves],
                    [
                        "Z - Zebra",
                        "A - Alpha",
                        "M - Mid",
                    ],
                )

                cmd = preview.heading("#0", "command")
                preview.tk.call(cmd)
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "text") for r in crate_leaves],
                    [
                        "A - Alpha",
                        "M - Mid",
                        "Z - Zebra",
                    ],
                )
                solo_leaves = list(preview.get_children(groups[1]))
                self.assertEqual(
                    [preview.item(r, "text") for r in solo_leaves],
                    ["O - Only"],
                )

                preview.tk.call(cmd)
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "text") for r in crate_leaves],
                    [
                        "Z - Zebra",
                        "M - Mid",
                        "A - Alpha",
                    ],
                )

                app.track_search_var.set("alpha")
                _flush_track_search_debounce(app)
                groups = preview.get_children("")
                self.assertEqual(len(groups), 1)
                leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "text") for r in leaves],
                    ["A - Alpha"],
                )

                app.track_search_var.set("")
                _flush_track_search_debounce(app)
                groups = preview.get_children("")
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "text") for r in crate_leaves],
                    [
                        "Z - Zebra",
                        "M - Mid",
                        "A - Alpha",
                    ],
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_bit_depth_sort_reorders_after_fill(self) -> None:
        """Sorting by bit depth re-applies after async header fill."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from unittest.mock import patch

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                deep = base / "Deep.flac"
                shallow = base / "Shallow.flac"
                deep.write_bytes(_flac_with_bit_depth(24))
                shallow.write_bytes(_flac_with_bit_depth(16))
                xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="Deep" Artist="D"
           Location="{encode_location(deep)}"
           Kind="FLAC File" SampleRate="44100"/>
    <TRACK TrackID="2" Name="Shallow" Artist="S"
           Location="{encode_location(shallow)}"
           Kind="FLAC File" SampleRate="44100"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Crate" Type="1" KeyType="0" Entries="2">
        <TRACK Key="1"/>
        <TRACK Key="2"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
                source = write_xml(base, xml)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                crate = tree.get_children("")[0]
                with app_patches(
                    **{"threading.Thread": {"side_effect": run_inline_thread}}
                ):
                    tree.selection_set(crate)
                    tree.event_generate("<<TreeviewSelect>>")
                    preview = app.tracklist_tree
                    preview.tk.call(preview.heading("bit_depth", "command"))
                    root.update()
                    leaves = list(preview.get_children(preview.get_children("")[0]))
                    self.assertEqual(
                        [preview.item(r, "values")[1] for r in leaves],
                        ["16", "24"],
                    )
                    self.assertEqual(
                        [preview.item(r, "text") for r in leaves],
                        ["S - Shallow", "D - Deep"],
                    )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_hover_shows_decoded_file_path(self) -> None:
        """Given a listed track with a Location: When hovering the row:
        Then a tooltip shows the decoded file path."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), TRACKLIST_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                dark = tree.get_children("")[0]
                tree.selection_set(dark)
                tree.event_generate("<<TreeviewSelect>>")
                preview = app.tracklist_tree
                group = preview.get_children("")[0]
                leaf = preview.get_children(group)[0]
                with patch.object(preview, "identify_row", return_value=leaf):
                    preview.event_generate("<Motion>", x=10, y=10)
                root.update_idletasks()
                texts: list[str] = []

                def collect(widget: tk.Misc) -> None:
                    for child in widget.winfo_children():
                        if isinstance(child, tk.Toplevel):
                            for inner in child.winfo_children():
                                try:
                                    texts.append(str(inner.cget("text")))
                                except tk.TclError:
                                    continue
                        collect(child)

                collect(root)
                self.assertIn("/Users/me/music/Bestial.flac", texts)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
