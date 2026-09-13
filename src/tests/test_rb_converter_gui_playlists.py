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

from cli_error import CliError
from gui_tk import app_patches, mark_output_folder_valid, run_inline_thread, startup_patches, tk_available
from gui_xml_fixtures import TRACKLIST_XML, write_xml

def _flush_debounced(app, attr: str, callback) -> None:
    """Cancel a pending search after() and apply immediately."""
    after_id = getattr(app, attr)
    if after_id is not None:
        app.root.after_cancel(after_id)
        setattr(app, attr, None)
    callback()

def _flush_playlist_search_debounce(app) -> None:
    _flush_debounced(app, "_playlist_search_after_id", app._apply_playlist_filter)

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

class GuiPlaylistExplorerTests(unittest.TestCase):
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

    def test_load_playlists_paints_folder_tree(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), NESTED_XML)
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

    def test_load_playlists_keeps_missing_xml_status(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), NESTED_XML)
                root, app = self._make_app(source)
                missing = Path(tmp) / "gone.xml"
                app.xml_var.set(str(missing))
                app._load_playlists()
                self.assertIn("XML not found", app.status_var.get())
                self.assertIn(str(missing), app.status_var.get())
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_load_playlists_keeps_cli_error_status(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), NESTED_XML)
                root, app = self._make_app(source)
                with app_patches(
                    **{
                        "load_dj_playlists": {
                            "side_effect": CliError("Invalid XML: broken")
                        }
                    }
                ):
                    app._load_playlists()
                self.assertEqual(app.status_var.get(), "Invalid XML: broken")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_folders_do_not_select_playlists(self) -> None:
        """Folders never contribute playlists: click expands only; programmatic
        folder selection is ignored; folder+child keeps only the playlist."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from unittest.mock import patch

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), NESTED_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                folder_iid = tree.get_children("")[1]
                nested_iid = tree.get_children(folder_iid)[0]

                self.assertFalse(tree.item(folder_iid, "open"))
                with patch.object(tree, "identify_row", return_value=folder_iid):
                    result = app._on_playlist_button1(type("E", (), {"y": 1})())
                self.assertEqual(result, "break")
                self.assertTrue(tree.item(folder_iid, "open"))
                self.assertEqual(tree.selection(), ())
                self.assertEqual(app._selected_playlists(), [])

                tree.selection_set(folder_iid)
                self.assertEqual(app._selected_playlists(), [])

                tree.selection_set(folder_iid, nested_iid)
                self.assertEqual(
                    app._selected_playlists(),
                    [("Intelligent playlists", "Nested")],
                )
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_selected_playlists_rejects_same_leaf_name(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), DUP_NAME_XML)
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
                with self.assertRaises(CliError) as ctx:
                    app._selected_playlists()
                self.assertIn("same name", str(ctx.exception))
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_convert_rejects_same_name_when_both_have_tracks(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), DUP_NAME_XML)
                root, app = self._make_app(source)
                tree = app.playlist_tree
                one = tree.get_children("")[0]
                two = tree.get_children("")[1]
                tree.selection_set(tree.get_children(one)[0], tree.get_children(two)[0])
                tree.event_generate("<<TreeviewSelect>>")
                # Default selection includes both groups' leaves.

                with app_patches(
                    {
                        "prepare_batch": None,
                        "threading.Thread": {"side_effect": run_inline_thread},
                        "messagebox.showerror": None,
                    }
                ) as mocks:
                    prepare = mocks["prepare_batch"]
                    showerror = mocks["messagebox.showerror"]
                    mark_output_folder_valid(app)
                    app._start_convert()
                    root.update()

                prepare.assert_not_called()
                showerror.assert_called()
                self.assertIn("same name", str(showerror.call_args).lower())
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_search_keeps_ancestors_of_matches(self) -> None:
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk

        root = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = write_xml(Path(tmp), NESTED_XML)
                root, app = self._make_app(source)
                app.search_var.set("nested")
                _flush_playlist_search_debounce(app)
                tree = app.playlist_tree
                top = tree.get_children("")
                self.assertEqual(len(top), 1)
                self.assertEqual(tree.item(top[0], "text"), "Intelligent playlists")
                self.assertTrue(tree.item(top[0], "open"))
                children = tree.get_children(top[0])
                self.assertEqual(len(children), 1)
                self.assertEqual(tree.item(children[0], "text"), "Nested (2 tracks)")
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_convert_worker_passes_shared_source_root_for_two_playlists(self) -> None:
        """Given two playlists: When _convert_worker runs: Then prepare gets the
        same source_root for each playlist (one shared XML parse)."""
        if not tk_available():
            self.skipTest("_tkinter not available")

        import tkinter as tk
        from unittest.mock import MagicMock, patch

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
                all_leaves = [
                    leaf for group in groups for leaf in preview.get_children(group)
                ]
                preview.selection_set(*all_leaves)
                preview.event_generate("<<TreeviewSelect>>")

                prepare_calls: list[tuple] = []

                def fake_prepare(*args, **kwargs):
                    prepare_calls.append((args, kwargs))
                    # First playlist succeeds so the worker continues to the second.
                    if len(prepare_calls) < 2:
                        return (MagicMock(), [])
                    return (None, ["stop"])

                with app_patches(
                    {
                        "prepare_batch": {"side_effect": fake_prepare},
                        "threading.Thread": {"side_effect": run_inline_thread},
                        "messagebox.showerror": None,
                    }
                ):
                    mark_output_folder_valid(app)
                    app._start_convert()
                    root.update()

                self.assertEqual(len(prepare_calls), 1)
                args, kwargs = prepare_calls[0]
                self.assertIsNotNone(kwargs.get("source_root"))
                playlist_refs = args[1]
                names = sorted(name for _folder, name in playlist_refs)
                self.assertEqual(names, ["Dark forest", "Morning"])
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()

    def test_convert_passes_selected_track_keys_to_prepare(self) -> None:
        """Deselected (including missing) Keys are not prepared; empty errors."""
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

                with app_patches(
                    {
                        "prepare_batch": {"side_effect": fake_prepare},
                        "threading.Thread": {"side_effect": run_inline_thread},
                        "messagebox.showerror": None,
                    }
                ):
                    mark_output_folder_valid(app)
                    app._start_convert()
                    root.update()  # _finish_error while showerror is patched

                self.assertEqual(len(prepare_calls), 1)
                args, kwargs = prepare_calls[0]
                self.assertEqual(args[1], [("", "Dark forest")])
                self.assertEqual(
                    set(kwargs.get("track_keys_by_playlist", {}).get(("", "Dark forest"), [])),
                    {"1", "2"},
                )

                preview.selection_remove(*preview.selection())
                preview.event_generate("<<TreeviewSelect>>")
                prepare_calls.clear()
                with app_patches(
                    {
                        "prepare_batch": {"side_effect": fake_prepare},
                        "messagebox.showerror": None,
                    }
                ) as mocks:
                    prepare = mocks["prepare_batch"]
                    showerror = mocks["messagebox.showerror"]
                    mark_output_folder_valid(app)
                    app._start_convert()
                    prepare.assert_not_called()
                    showerror.assert_called()
                    self.assertIn("track", str(showerror.call_args).lower())
        except tk.TclError:
            self.skipTest("tk.TclError: display not available")
        finally:
            if root is not None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
