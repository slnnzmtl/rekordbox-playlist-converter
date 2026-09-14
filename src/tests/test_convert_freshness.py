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

from convert.freshness import metadata_signature, source_signature


class SourceSignatureTests(unittest.TestCase):
    def test_source_signature_records_size_and_mtime_without_writing(self) -> None:
        """Given a source file: When source_signature runs: Then size and
        mtime_ns match stat and the file bytes are unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.flac"
            path.write_bytes(b"fLaC-data")
            before = path.read_bytes()
            st = path.stat()
            sig = source_signature(path)
            self.assertEqual(sig["size"], st.st_size)
            self.assertEqual(sig["mtime_ns"], st.st_mtime_ns)
            self.assertIsNone(sig["hash"])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mtime_ns, st.st_mtime_ns)


class MetadataSignatureTests(unittest.TestCase):
    def test_metadata_signature_ignores_output_fields_and_tracks_cues(self) -> None:
        """Given TRACK elements: When metadata_signature runs: Then Name and
        cue changes differ, while Location and TrackID do not."""
        a = ET.Element(
            "TRACK",
            {
                "TrackID": "1",
                "Name": "Song",
                "Artist": "DJ",
                "Location": "file://localhost/a.flac",
                "Kind": "FLAC File",
                "Size": "10",
                "BitRate": "1000",
                "SampleRate": "44100",
            },
        )
        ET.SubElement(a, "TEMPO", {"Inizio": "0.001", "Bpm": "128.00"})
        b = ET.Element("TRACK", dict(a.attrib))
        ET.SubElement(b, "TEMPO", {"Inizio": "0.001", "Bpm": "128.00"})
        self.assertEqual(metadata_signature(a), metadata_signature(b))

        relocated = ET.Element("TRACK", dict(a.attrib))
        relocated.set("TrackID", "99")
        relocated.set("Location", "file://localhost/other.flac")
        ET.SubElement(relocated, "TEMPO", {"Inizio": "0.001", "Bpm": "128.00"})
        self.assertEqual(metadata_signature(a), metadata_signature(relocated))

        renamed = ET.Element("TRACK", dict(a.attrib))
        renamed.set("Name", "Other")
        ET.SubElement(renamed, "TEMPO", {"Inizio": "0.001", "Bpm": "128.00"})
        self.assertNotEqual(metadata_signature(a), metadata_signature(renamed))

        cued = ET.Element("TRACK", dict(a.attrib))
        ET.SubElement(cued, "TEMPO", {"Inizio": "0.001", "Bpm": "128.00"})
        ET.SubElement(cued, "POSITION_MARK", {"Name": "Cue", "Start": "1.0"})
        self.assertNotEqual(metadata_signature(a), metadata_signature(cued))
        sig = metadata_signature(a)
        self.assertTrue(sig.startswith("sha256:"))
        self.assertEqual(len(sig), len("sha256:") + 64)


if __name__ == "__main__":
    unittest.main()
