#!/usr/bin/env python3
"""apply_xml generated-playlist sequence sync (reorder / remove / insert / repeats)."""

from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from convert.models import Plan, PlannedTrack
from convert.paths import source_key
from rekordbox_xml import encode_location
from xml_output import PlaylistApplyResult, apply_xml, playlist_keys


class ApplyXmlPlaylistSyncTests(unittest.TestCase):
    def _item(
        self,
        name: str,
        dest: Path,
        *,
        source: Path | None = None,
    ) -> PlannedTrack:
        src = source or Path(f"/music/{name}.flac")
        return PlannedTrack(
            source_el=ET.Element(
                "TRACK",
                {"Name": name, "Artist": "A", "Location": encode_location(src)},
            ),
            source_path=src,
            dest_path=dest,
            dest_location=encode_location(dest),
            dest_name=dest.name,
            codec=None,
            passthrough=True,
            noop=False,
            output_format="wav",
        )

    def _plan_with_existing(
        self,
        root: Path,
        items: list[PlannedTrack],
        existing_keys: list[str],
        *,
        tracks: list[PlannedTrack] | None = None,
    ) -> Plan:
        output_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(
            output_root, "PRODUCT", {"Name": "rekordbox", "Version": "6.8.5"}
        )
        collection = ET.SubElement(output_root, "COLLECTION", {"Entries": "0"})
        for i, item in enumerate(items, start=1):
            ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": str(i),
                    "Name": item.source_el.get("Name", ""),
                    "Location": item.dest_location,
                    "Kind": "WAV File",
                },
            )
        collection.set("Entries", str(len(items)))
        playlists = ET.SubElement(output_root, "PLAYLISTS")
        root_node = ET.SubElement(
            playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"}
        )
        pl = ET.SubElement(
            root_node,
            "NODE",
            {
                "Name": "P [WAV]",
                "Type": "1",
                "KeyType": "0",
                "Entries": str(len(existing_keys)),
            },
        )
        for key in existing_keys:
            ET.SubElement(pl, "TRACK", {"Key": key})
        track_list = tracks if tracks is not None else items
        return Plan(
            playlist_name="P",
            wav_playlist_name="P [WAV]",
            library_dir=root,
            media_dir=root / "WAV",
            output=root / "import.xml",
            tracks=track_list,
            unique=items,
            source_root=ET.Element("DJ_PLAYLISTS"),
            output_root=output_root,
            output_existed=True,
            output_format="wav",
        )

    def test_apply_xml_reorders_existing_playlist_on_complete_success(self) -> None:
        """Given existing [1,2] and desired [2,1] with full success: When
        apply_xml runs: Then playlist keys become [2,1]."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d1 = root / "WAV" / "A.wav"
            d2 = root / "WAV" / "B.wav"
            d1.parent.mkdir(parents=True)
            d1.write_bytes(b"RIFF")
            d2.write_bytes(b"RIFF")
            a = self._item("A", d1)
            b = self._item("B", d2)
            plan = self._plan_with_existing(root, [a, b], ["1", "2"], tracks=[b, a])
            success = {
                (source_key(a.source_path), "wav"),
                (source_key(b.source_path), "wav"),
            }
            with patch("xml_output.probe_dest_tech", return_value=("1", "1411", "44100")):
                result = apply_xml(plan, success)
            pl = plan.output_root.find(".//NODE[@Name='P [WAV]']")
            assert pl is not None
            self.assertEqual(playlist_keys(pl), ["2", "1"])
            self.assertIsInstance(result, PlaylistApplyResult)
            self.assertTrue(result.fully_synced)
            self.assertTrue(result.reordered)
            self.assertEqual(result.appended, 0)
            self.assertEqual(result.removed, 0)

    def test_apply_xml_removes_dropped_keys_on_complete_success(self) -> None:
        """Given existing [1,2,3] and desired [1,2]: When apply_xml runs with
        full success: Then playlist keys become [1,2]."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = []
            for name in ("A", "B", "C"):
                dest = root / "WAV" / f"{name}.wav"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"RIFF")
                items.append(self._item(name, dest))
            plan = self._plan_with_existing(
                root, items, ["1", "2", "3"], tracks=items[:2]
            )
            success = {(source_key(i.source_path), "wav") for i in items[:2]}
            with patch("xml_output.probe_dest_tech", return_value=("1", "1411", "44100")):
                result = apply_xml(plan, success)
            pl = plan.output_root.find(".//NODE[@Name='P [WAV]']")
            assert pl is not None
            self.assertEqual(playlist_keys(pl), ["1", "2"])
            self.assertTrue(result.fully_synced)
            self.assertEqual(result.removed, 1)
            self.assertEqual(len(plan.output_root.findall("COLLECTION/TRACK")), 3)

    def test_apply_xml_inserts_before_existing_on_complete_success(self) -> None:
        """Given existing [1,2] and desired [3,1,2]: When apply_xml runs with
        full success: Then playlist keys become [3,1,2]."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = []
            for name in ("A", "B", "C"):
                dest = root / "WAV" / f"{name}.wav"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"RIFF")
                items.append(self._item(name, dest))
            # Collection starts with A,B only; C is new
            plan = self._plan_with_existing(
                root, items[:2], ["1", "2"], tracks=[items[2], items[0], items[1]]
            )
            plan.unique = items
            success = {(source_key(i.source_path), "wav") for i in items}
            with patch("xml_output.probe_dest_tech", return_value=("1", "1411", "44100")):
                result = apply_xml(plan, success)
            pl = plan.output_root.find(".//NODE[@Name='P [WAV]']")
            assert pl is not None
            self.assertEqual(playlist_keys(pl), ["3", "1", "2"])
            self.assertTrue(result.fully_synced)
            self.assertEqual(result.appended, 1)

    def test_apply_xml_preserves_repeated_keys_on_complete_success(self) -> None:
        """Given desired [1,2,1]: When apply_xml runs with full success: Then
        playlist Keys are [1,2,1]."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d1 = root / "WAV" / "A.wav"
            d2 = root / "WAV" / "B.wav"
            d1.parent.mkdir(parents=True)
            d1.write_bytes(b"RIFF")
            d2.write_bytes(b"RIFF")
            a = self._item("A", d1)
            b = self._item("B", d2)
            plan = self._plan_with_existing(
                root, [a, b], ["1", "2"], tracks=[a, b, a]
            )
            success = {
                (source_key(a.source_path), "wav"),
                (source_key(b.source_path), "wav"),
            }
            with patch("xml_output.probe_dest_tech", return_value=("1", "1411", "44100")):
                result = apply_xml(plan, success)
            pl = plan.output_root.find(".//NODE[@Name='P [WAV]']")
            assert pl is not None
            self.assertEqual(playlist_keys(pl), ["1", "2", "1"])
            self.assertTrue(result.fully_synced)

    def test_apply_xml_preserves_existing_keys_when_incomplete(self) -> None:
        """Given existing [1,2], desired reorder, but one track not in success:
        When apply_xml runs: Then keys stay [1,2] and fully_synced is False."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d1 = root / "WAV" / "A.wav"
            d2 = root / "WAV" / "B.wav"
            d1.parent.mkdir(parents=True)
            d1.write_bytes(b"RIFF")
            d2.write_bytes(b"RIFF")
            a = self._item("A", d1)
            b = self._item("B", d2)
            plan = self._plan_with_existing(root, [a, b], ["1", "2"], tracks=[b, a])
            # Only A succeeded; B conflicted / failed
            success = {(source_key(a.source_path), "wav")}
            with patch("xml_output.probe_dest_tech", return_value=("1", "1411", "44100")):
                result = apply_xml(plan, success)
            pl = plan.output_root.find(".//NODE[@Name='P [WAV]']")
            assert pl is not None
            self.assertEqual(playlist_keys(pl), ["1", "2"])
            self.assertFalse(result.fully_synced)
            self.assertEqual(result.appended, 0)


if __name__ == "__main__":
    unittest.main()
