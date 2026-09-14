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

from convert.freshness import (
    assignment_state,
    mark_complete,
    mark_incomplete,
    metadata_signature,
    output_signature,
    recipe_from_item,
    source_signature,
)
from convert.models import PlannedTrack


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
            self.assertNotIn("hash", sig)
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


def _item(*, output_format: str, bit_depth: int, sample_rate: int) -> PlannedTrack:
    el = ET.Element("TRACK", {"Name": "Song"})
    dest = Path(f"/tmp/{output_format}/out.{output_format}")
    return PlannedTrack(
        source_el=el,
        source_path=Path("/tmp/src.flac"),
        dest_path=dest,
        dest_location="file://localhost/out",
        dest_name=dest.name,
        codec=None,
        passthrough=False,
        noop=False,
        bit_depth=bit_depth,
        sample_rate=sample_rate,
        output_format=output_format,
    )


class RecipeTests(unittest.TestCase):
    def test_recipe_from_item_uses_effective_quality_without_passthrough(self) -> None:
        """Given planned tracks: When recipe_from_item runs: Then format, depth,
        rate, channels, and revision are stored and passthrough is omitted."""
        wav = recipe_from_item(
            _item(output_format="wav", bit_depth=16, sample_rate=44100)
        )
        self.assertEqual(
            wav,
            {
                "format": "wav",
                "bit_depth": 16,
                "sample_rate": 44100,
                "channels": 2,
                "revision": 1,
            },
        )
        self.assertNotIn("passthrough", wav)
        aiff = recipe_from_item(
            _item(output_format="aiff", bit_depth=24, sample_rate=48000)
        )
        self.assertEqual(aiff["format"], "aiff")
        self.assertEqual(aiff["bit_depth"], 24)
        self.assertEqual(aiff["sample_rate"], 48000)
        self.assertEqual(aiff["channels"], 2)
        self.assertEqual(aiff["revision"], 1)


class AssignmentStateTests(unittest.TestCase):
    def test_missing_freshness_is_unverified_and_complete_requires_all_fields(
        self,
    ) -> None:
        """Given dest-only, incomplete, or partial records: When assignment_state
        runs: Then only a full complete record is complete."""
        dest_only = {"dest": "WAV/A.wav"}
        self.assertEqual(assignment_state(dest_only), "unverified")
        incomplete = {
            "dest": "WAV/A.wav",
            "state": "incomplete",
            "source": {"size": 1, "mtime_ns": 1},
        }
        self.assertEqual(assignment_state(incomplete), "incomplete")
        partial = {
            "dest": "WAV/A.wav",
            "state": "complete",
            "source": {"size": 1, "mtime_ns": 1},
        }
        self.assertEqual(assignment_state(partial), "unverified")

        record = {"dest": "WAV/A.wav"}
        mark_incomplete(record)
        self.assertEqual(record["state"], "incomplete")
        self.assertEqual(assignment_state(record), "incomplete")

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.flac"
            dest = Path(tmp) / "A.wav"
            src.write_bytes(b"src")
            dest.write_bytes(b"dest-audio")
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = _item(output_format="wav", bit_depth=24, sample_rate=48000)
            mark_complete(
                record,
                source=source_signature(src),
                metadata=metadata_signature(el),
                output=output_signature(dest),
                recipe=recipe_from_item(item),
            )
        self.assertEqual(record["state"], "complete")
        self.assertEqual(assignment_state(record), "complete")
        self.assertEqual(record["source"]["size"], 3)
        self.assertEqual(record["output"]["size"], 10)
        self.assertTrue(record["metadata"]["signature"].startswith("sha256:"))
        self.assertEqual(record["recipe"]["format"], "wav")


if __name__ == "__main__":
    unittest.main()
