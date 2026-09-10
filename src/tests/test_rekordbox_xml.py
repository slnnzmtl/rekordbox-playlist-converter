#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import rb_playlist_to_wav as rb


class LocationTests(unittest.TestCase):
    def test_roundtrip_percent_encoding(self) -> None:
        path = Path("/Users/me/It's just a bad dream/07 - Bestial.flac")
        url = rb.encode_location(path)
        self.assertTrue(url.startswith("file://localhost/"))
        self.assertIn("It%27s", url)
        self.assertIn("%20", url)
        self.assertEqual(rb.decode_location(url), path)

    def test_decode_file_triple_slash(self) -> None:
        self.assertEqual(
            rb.decode_location("file:///Users/me/track.flac"),
            Path("/Users/me/track.flac"),
        )

    def test_invalid_url(self) -> None:
        self.assertIsNone(rb.decode_location("http://example.com/a.flac"))
        self.assertIsNone(rb.decode_location(""))
        self.assertIsNone(rb.decode_location("C:/not/a/url.flac"))


class PlaylistXmlHelperTests(unittest.TestCase):
    def test_discover_xml_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            hit = cwd / "rekordbox.xml"
            hit.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            (cwd / "other.txt").write_text("x", encoding="utf-8")
            found = rb.discover_xml_candidates(
                cwd,
                candidates=(Path("rekordbox.xml"), Path("missing.xml")),
            )
            self.assertEqual(found, [hit.resolve()])

    def test_discover_xml_candidates_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                rb.discover_xml_candidates(
                    Path(tmp), candidates=(Path("rekordbox.xml"),)
                ),
                [],
            )

    def test_discover_xml_candidates_skips_documents_when_not_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            documents = home / "Documents" / "rekordbox"
            documents.mkdir(parents=True)
            docs_xml = documents / "rekordbox.xml"
            docs_xml.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            cwd = home / "cwd"
            cwd.mkdir()
            local = cwd / "rekordbox.xml"
            local.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            found = rb.discover_xml_candidates(
                cwd,
                candidates=(
                    Path("rekordbox.xml"),
                    home / "Documents" / "rekordbox" / "rekordbox.xml",
                ),
                documents_accessible=False,
                home=home,
            )
            self.assertEqual(found, [local.resolve()])

    def test_discover_xml_candidates_includes_documents_when_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            documents = home / "Documents" / "rekordbox"
            documents.mkdir(parents=True)
            docs_xml = documents / "rekordbox.xml"
            docs_xml.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            found = rb.discover_xml_candidates(
                home,
                candidates=(home / "Documents" / "rekordbox" / "rekordbox.xml",),
                documents_accessible=True,
                home=home,
            )
            self.assertEqual(found, [docs_xml.resolve()])

    def test_iter_playlists_nested_folders(self) -> None:
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="0"/>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Top" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
      <NODE Name="Intelligent playlists" Type="0" Count="1">
        <NODE Name="Nested" Type="1" KeyType="0" Entries="2">
          <TRACK Key="1"/><TRACK Key="2"/>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        root = ET.fromstring(xml)
        entries = rb.iter_playlists(root)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "")
        self.assertEqual(entries[0][1], "Top")
        self.assertEqual(entries[1][0], "Intelligent playlists")
        self.assertEqual(entries[1][1], "Nested")
        self.assertEqual(rb.playlist_track_count(entries[1][2]), 2)

    def test_resolve_playlist_disambiguates_same_leaf_name(self) -> None:
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="0"/>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="2">
      <NODE Name="Selections" Type="0" Count="1">
        <NODE Name="Night" Type="0" Count="1">
          <NODE Name="Darkprog" Type="1" KeyType="0" Entries="5"/>
        </NODE>
      </NODE>
      <NODE Name="Genres" Type="0" Count="1">
        <NODE Name="Psychedelic" Type="0" Count="1">
          <NODE Name="Progressive" Type="0" Count="1">
            <NODE Name="Darkprog" Type="1" KeyType="0" Entries="671"/>
          </NODE>
        </NODE>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        root = ET.fromstring(xml)
        found, errors = rb.resolve_playlist(root, "Darkprog")
        self.assertIsNone(found)
        self.assertEqual(len(errors), 1)
        self.assertIn("duplicate playlist name: Darkprog", errors[0])
        self.assertIn("Selections / Night / Darkprog", errors[0])
        self.assertIn("Genres / Psychedelic / Progressive / Darkprog", errors[0])

        found, errors = rb.resolve_playlist(
            root, "Darkprog", folder="Selections / Night"
        )
        self.assertEqual(errors, [])
        assert found is not None
        self.assertEqual(found[0], "Selections / Night")
        self.assertEqual(found[1], "Darkprog")
        self.assertEqual(found[2].get("Entries"), "5")

        found, errors = rb.resolve_playlist(
            root, "Genres / Psychedelic / Progressive / Darkprog"
        )
        self.assertEqual(errors, [])
        assert found is not None
        self.assertEqual(found[2].get("Entries"), "671")

    def test_playlist_label(self) -> None:
        self.assertEqual(rb.playlist_label("", "Top"), "Top")
        self.assertEqual(rb.playlist_label("Selections / Night", "Darkprog"), "Selections / Night / Darkprog")

    def test_parse_selection_single_and_multi(self) -> None:
        entries = [
            ("", "A", ET.Element("NODE")),
            ("f", "B", ET.Element("NODE")),
            ("f", "C", ET.Element("NODE")),
        ]
        chosen, errors = rb.parse_playlist_selection("1", entries)
        self.assertEqual(errors, [])
        self.assertEqual([n for _f, n, _e in chosen], ["A"])
        chosen, errors = rb.parse_playlist_selection("1,3", entries)
        self.assertEqual(errors, [])
        self.assertEqual([n for _f, n, _e in chosen], ["A", "C"])
        chosen, errors = rb.parse_playlist_selection("all", entries)
        self.assertEqual(errors, [])
        self.assertEqual(len(chosen), 3)

    def test_parse_selection_rejects_duplicate_names(self) -> None:
        entries = [
            ("one", "Same", ET.Element("NODE")),
            ("two", "Same", ET.Element("NODE")),
            ("", "Other", ET.Element("NODE")),
        ]
        chosen, errors = rb.parse_playlist_selection("1,2", entries)
        self.assertEqual(chosen, [])
        self.assertTrue(any("same name" in e for e in errors))
        chosen, errors = rb.parse_playlist_selection("all", entries)
        self.assertEqual(chosen, [])
        self.assertTrue(any("same name" in e for e in errors))

    def test_parse_selection_out_of_range(self) -> None:
        entries = [("", "A", ET.Element("NODE"))]
        _, errors = rb.parse_playlist_selection("2", entries)
        self.assertTrue(any("out of range" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
