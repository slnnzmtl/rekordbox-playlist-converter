#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from convert.models import PlannedTrack
from convert.rerun import classify_assignment


def _item(*, noop: bool = False, output_format: str = "wav") -> PlannedTrack:
    el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
    dest = Path("/lib") / ("WAV" if output_format == "wav" else "AIFF") / "A.wav"
    if output_format == "aiff":
        dest = dest.with_suffix(".aiff")
    return PlannedTrack(
        source_el=el,
        source_path=Path("/music/a.flac"),
        dest_path=dest,
        dest_location="file://localhost/A",
        dest_name=dest.name,
        codec=None,
        passthrough=False,
        noop=noop,
        bit_depth=24,
        sample_rate=48000,
        output_format=output_format,
    )


class ClassifyAssignmentTests(unittest.TestCase):
    def test_source_equals_dest_is_in_place_noop(self) -> None:
        """Given an in-place planned track: When classify: Then in_place_noop
        even if force is set."""
        item = _item(noop=True)
        self.assertEqual(
            classify_assignment(
                item=item,
                record={"dest": "WAV/A.wav", "state": "complete"},
                force=False,
                dest_exists=True,
                dest_stat={"size": 1, "mtime_ns": 1},
                source_stat={"size": 1, "mtime_ns": 1},
            ),
            "in_place_noop",
        )
        self.assertEqual(
            classify_assignment(
                item=item,
                record=None,
                force=True,
                dest_exists=True,
                dest_stat={"size": 1, "mtime_ns": 1},
                source_stat={"size": 1, "mtime_ns": 1},
            ),
            "in_place_noop",
        )


if __name__ == "__main__":
    unittest.main()
