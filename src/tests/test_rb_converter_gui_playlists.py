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
           Location="file://localhost/Users/me/music/Bestial.flac"
           Kind="FLAC File" SampleRate="44100"/>
    <TRACK TrackID="2" Name="Revelation" Artist="Shogan"
           Location="file://localhost/Users/me/music/Revelation.aiff"
           Kind="AIFF File" SampleRate="48000"/>
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
                self.assertEqual(
                    preview.cget("columns"),
                    ("format", "bit_depth", "sample_rate"),
                )
                self.assertEqual(preview.heading("#0", "text"), "Track")
                self.assertEqual(preview.heading("format", "text"), "Format")
                self.assertEqual(preview.heading("bit_depth", "text"), "Bit depth")
                self.assertEqual(preview.heading("sample_rate", "text"), "Sample rate")
                for col in ("#0", "format", "bit_depth", "sample_rate"):
                    self.assertEqual(str(preview.heading(col, "anchor")), "w")

                groups = preview.get_children("")
                self.assertEqual(preview.item(groups[0], "text"), "Dark forest (3 tracks)")
                leaves = preview.get_children(groups[0])
                self.assertEqual(
                    [preview.item(r, "text") for r in leaves],
                    [
                        "ABSL - Bestial.flac",
                        "Shogan - Revelation.aiff",
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

    def test_tracklist_selection_follows_leaves_and_group_header(self) -> None:
        """Listed leaves start selected; deselect updates status; header remaps."""
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
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_convert_passes_selected_track_keys_to_prepare(self) -> None:
        """Deselected (including missing) Keys are not prepared; empty errors."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from unittest.mock import patch

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = _write_xml(Path(tmp), TRACKLIST_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                dark, morning = tree.get_children("")
                tree.selection_set(dark, morning)
                tree.event_generate("<<TreeviewSelect>>")

                preview = app.tracklist_tree
                groups = preview.get_children("")
                dark_leaves = list(preview.get_children(groups[0]))
                # Keep Key 1 + 2 from Dark forest; drop missing Key 999 and all Morning.
                preview.selection_set(dark_leaves[0], dark_leaves[1])
                preview.event_generate("<<TreeviewSelect>>")

                prepare_calls: list[tuple] = []

                def fake_prepare(*args, **kwargs):
                    prepare_calls.append((args, kwargs))
                    return (None, ["stop"])

                def run_inline(target=None, **_kwargs):
                    class _T:
                        def start(self_inner):
                            target()

                    return _T()

                with patch(
                    "rb_converter_gui.rb.prepare", side_effect=fake_prepare
                ), patch(
                    "rb_converter_gui.threading.Thread", side_effect=run_inline
                ), patch("rb_converter_gui.messagebox.showerror"):
                    app._start_convert()
                    root.update()  # _finish_error while showerror is patched

                self.assertEqual(len(prepare_calls), 1)
                args, kwargs = prepare_calls[0]
                self.assertEqual(args[1], "Dark forest")
                self.assertEqual(set(kwargs.get("track_keys") or ()), {"1", "2"})

                preview.selection_remove(*preview.selection())
                preview.event_generate("<<TreeviewSelect>>")
                prepare_calls.clear()
                with patch(
                    "rb_converter_gui.rb.prepare", side_effect=fake_prepare
                ) as prepare, patch(
                    "rb_converter_gui.messagebox.showerror"
                ) as showerror:
                    app._start_convert()
                    prepare.assert_not_called()
                    showerror.assert_called()
                    self.assertIn("track", str(showerror.call_args).lower())
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_track_search_filters_listed_rows_and_clears(self) -> None:
        """Track search matches preview labels; clear restores the listed set."""
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
                tree.selection_set(dark, morning)
                tree.event_generate("<<TreeviewSelect>>")

                preview = app.tracklist_tree
                app.track_search_var.set("revelation")
                groups = preview.get_children("")
                self.assertEqual(len(groups), 1)
                leaves = preview.get_children(groups[0])
                self.assertEqual(
                    [preview.item(r, "text") for r in leaves],
                    ["Shogan - Revelation.aiff"],
                )
                self.assertEqual(
                    app.status_var.get(),
                    "1 unique tracks from 1 playlist",
                )

                app.track_search_var.set("")
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
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import rb_playlist_to_wav as rb
        from unittest.mock import patch

        def run_inline(target=None, **_kwargs):
            class _T:
                def start(self_inner):
                    target()

            return _T()

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
           Location="{rb.encode_location(flac)}"
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
                source = _write_xml(base, xml)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                crate = tree.get_children("")[0]
                with patch(
                    "rb_converter_gui.threading.Thread", side_effect=run_inline
                ):
                    tree.selection_set(crate)
                    tree.event_generate("<<TreeviewSelect>>")
                    preview = app.tracklist_tree
                    leaves = preview.get_children(preview.get_children("")[0])
                    self.assertEqual(
                        [preview.item(r, "values") for r in leaves],
                        [("FLAC", "—", "44100")],
                    )
                    root.update()
                    leaves = preview.get_children(preview.get_children("")[0])
                    self.assertEqual(
                        [preview.item(r, "values") for r in leaves],
                        [("FLAC", "24", "44100")],
                    )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_bit_depth_cache_clears_on_xml_refresh(self) -> None:
        """XML reload drops the session cache; first paint is — again."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import rb_playlist_to_wav as rb
        from unittest.mock import patch

        def run_inline(target=None, **_kwargs):
            class _T:
                def start(self_inner):
                    target()

            return _T()

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
           Location="{rb.encode_location(flac)}"
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
                source = _write_xml(base, xml)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                with patch(
                    "rb_converter_gui.threading.Thread", side_effect=run_inline
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
        if not _tk_available():
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
                source = _write_xml(Path(tmp), MIXED_XML)
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
                    ["ABSL - Bestial.flac", "(missing track)"],
                )

                app.track_search_var.set("mp")
                groups = preview.get_children("")
                self.assertEqual(groups, ())

                app.track_search_var.set("")
                tree.selection_set(mixed)
                tree.event_generate("<<TreeviewSelect>>")
                leaves = preview.get_children(preview.get_children("")[0])
                preview.selection_set(*leaves)
                preview.event_generate("<<TreeviewSelect>>")

                prepare_calls: list[tuple] = []

                def fake_prepare(*args, **kwargs):
                    prepare_calls.append((args, kwargs))
                    return (None, ["stop"])

                def run_inline(target=None, **_kwargs):
                    class _T:
                        def start(self_inner):
                            target()

                    return _T()

                with patch(
                    "rb_converter_gui.rb.prepare", side_effect=fake_prepare
                ), patch(
                    "rb_converter_gui.threading.Thread", side_effect=run_inline
                ), patch("rb_converter_gui.messagebox.showerror"):
                    app._start_convert()
                    root.update()

                self.assertEqual(len(prepare_calls), 1)
                _args, kwargs = prepare_calls[0]
                keys = set(kwargs.get("track_keys") or ())
                self.assertIn("1", keys)
                self.assertNotIn("2", keys)
                self.assertNotIn("3", keys)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_browser_panes_default_sash_at_30_percent(self) -> None:
        """One-shot sashpos places the playlist pane at ~30% of the paned width."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = _write_xml(Path(tmp), TRACKLIST_XML)
                root, app = self._make_app(source)
                root.geometry("1120x720")
                root.deiconify()
                root.update_idletasks()
                root.update()
                panes = app.browser_panes
                width = panes.winfo_width()
                if width <= 1:
                    self.skipTest("panedwindow width not realized")
                sash = panes.sashpos(0)
                self.assertAlmostEqual(sash / width, 0.3, delta=0.05)
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_heading_sorts_within_groups(self) -> None:
        """Heading clicks sort leaves inside each group; reverse; survives search."""
        if not _tk_available():
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
                source = _write_xml(Path(tmp), SORT_XML)
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
                        "Z - Zebra.flac",
                        "A - Alpha.aiff",
                        "M - Mid.wav",
                    ],
                )

                cmd = preview.heading("#0", "command")
                self.assertTrue(str(cmd))
                preview.tk.call(cmd)
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "text") for r in crate_leaves],
                    [
                        "A - Alpha.aiff",
                        "M - Mid.wav",
                        "Z - Zebra.flac",
                    ],
                )
                solo_leaves = list(preview.get_children(groups[1]))
                self.assertEqual(
                    [preview.item(r, "text") for r in solo_leaves],
                    ["O - Only.flac"],
                )

                preview.tk.call(cmd)
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "text") for r in crate_leaves],
                    [
                        "Z - Zebra.flac",
                        "M - Mid.wav",
                        "A - Alpha.aiff",
                    ],
                )

                fmt_cmd = preview.heading("format", "command")
                preview.tk.call(fmt_cmd)
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "values")[0] for r in crate_leaves],
                    ["AIFF", "FLAC", "WAV"],
                )

                rate_cmd = preview.heading("sample_rate", "command")
                preview.tk.call(rate_cmd)
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "values")[2] for r in crate_leaves],
                    ["44100", "48000", "48000"],
                )

                app.track_search_var.set("alpha")
                groups = preview.get_children("")
                self.assertEqual(len(groups), 1)
                leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "text") for r in leaves],
                    ["A - Alpha.aiff"],
                )

                app.track_search_var.set("")
                groups = preview.get_children("")
                crate_leaves = list(preview.get_children(groups[0]))
                self.assertEqual(
                    [preview.item(r, "values")[2] for r in crate_leaves],
                    ["44100", "48000", "48000"],
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_tracklist_bit_depth_sort_reorders_after_fill(self) -> None:
        """Sorting by bit depth re-applies after async header fill."""
        if not _tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        import rb_playlist_to_wav as rb
        from unittest.mock import patch

        def run_inline(target=None, **_kwargs):
            class _T:
                def start(self_inner):
                    target()

            return _T()

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
           Location="{rb.encode_location(deep)}"
           Kind="FLAC File" SampleRate="44100"/>
    <TRACK TrackID="2" Name="Shallow" Artist="S"
           Location="{rb.encode_location(shallow)}"
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
                source = _write_xml(base, xml)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                crate = tree.get_children("")[0]
                with patch(
                    "rb_converter_gui.threading.Thread", side_effect=run_inline
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
                        ["S - Shallow.flac", "D - Deep.flac"],
                    )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
