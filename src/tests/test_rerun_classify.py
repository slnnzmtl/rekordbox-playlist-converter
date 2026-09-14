#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from convert.freshness import metadata_signature, recipe_from_item
from convert.models import PlannedTrack
from convert.rerun import classify_assignment


def _item(
    *,
    noop: bool = False,
    output_format: str = "wav",
    passthrough: bool = False,
    bit_depth: int = 24,
    sample_rate: int = 48000,
) -> PlannedTrack:
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
        passthrough=passthrough,
        noop=noop,
        bit_depth=bit_depth,
        sample_rate=sample_rate,
        output_format=output_format,
    )


def _complete_record(item: PlannedTrack, *, revision: int = 1) -> dict:
    recipe = recipe_from_item(item)
    recipe["revision"] = revision
    return {
        "dest": "WAV/A.wav" if item.output_format == "wav" else "AIFF/A.aiff",
        "state": "complete",
        "source": {"size": 10, "mtime_ns": 1, "hash": None},
        "metadata": {"signature": metadata_signature(item.source_el)},
        "output": {"size": 20, "mtime_ns": 2, "hash": None},
        "recipe": recipe,
    }


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

    def test_missing_dest_is_recreate_missing(self) -> None:
        """Given a reserved dest that is absent: When classify: Then
        recreate_missing even if the record looks complete."""
        item = _item()
        self.assertEqual(
            classify_assignment(
                item=item,
                record={
                    "dest": "WAV/A.wav",
                    "state": "complete",
                    "source": {"size": 1, "mtime_ns": 1},
                    "metadata": {"signature": "sha256:" + ("ab" * 32)},
                    "output": {"size": 2, "mtime_ns": 2},
                    "recipe": {
                        "format": "wav",
                        "bit_depth": 24,
                        "sample_rate": 48000,
                        "channels": 2,
                        "revision": 1,
                    },
                },
                force=False,
                dest_exists=False,
                dest_stat=None,
                source_stat={"size": 1, "mtime_ns": 1},
            ),
            "recreate_missing",
        )

    def test_complete_output_mismatch_is_conflict_even_under_force(self) -> None:
        """Given a complete record whose dest stats differ: When classify:
        Then external_modification_conflict including under force."""
        item = _item()
        record = _complete_record(item)
        kwargs = dict(
            item=item,
            record=record,
            dest_exists=True,
            dest_stat={"size": 99, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(
            classify_assignment(**kwargs, force=False),
            "external_modification_conflict",
        )
        self.assertEqual(
            classify_assignment(**kwargs, force=True),
            "external_modification_conflict",
        )

    def test_unverified_dest_is_not_reuse(self) -> None:
        """Given dest-only unverified assignment: When classify: Then transcode
        rather than reuse a CDJ-looking dest."""
        item = _item()
        self.assertEqual(
            classify_assignment(
                item=item,
                record={"dest": "WAV/A.wav"},
                force=False,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "transcode",
        )
        passthrough = _item(passthrough=True)
        self.assertEqual(
            classify_assignment(
                item=passthrough,
                record={"dest": "WAV/A.wav"},
                force=False,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "rewrite_container",
        )

    def test_force_rebuilds_audio_instead_of_metadata_or_reuse(self) -> None:
        """Given a complete matching record: When force classify: Then transcode
        even if unforced would reuse or refresh_xml."""
        item = _item()
        record = _complete_record(item)
        self.assertEqual(
            classify_assignment(
                item=item,
                record=record,
                force=True,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "transcode",
        )
        item.source_el.set("Name", "New")
        self.assertEqual(
            classify_assignment(
                item=item,
                record=record,
                force=True,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "transcode",
        )

    def test_source_recipe_and_metadata_changes(self) -> None:
        """Given complete records: When source, recipe, or metadata differ: Then
        the matching rebuild or refresh action is chosen."""
        item = _item()
        record = _complete_record(item)
        self.assertEqual(
            classify_assignment(
                item=item,
                record=record,
                force=False,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 11, "mtime_ns": 1},
            ),
            "transcode",
        )
        deeper = _item(bit_depth=16, sample_rate=44100)
        self.assertEqual(
            classify_assignment(
                item=deeper,
                record=_complete_record(_item()),
                force=False,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "transcode",
        )
        self.assertEqual(
            classify_assignment(
                item=item,
                record=_complete_record(item, revision=2),
                force=False,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "rewrite_container",
        )
        wav = _item()
        wav.source_el.set("Name", "New")
        self.assertEqual(
            classify_assignment(
                item=wav,
                record=_complete_record(_item()),
                force=False,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "refresh_xml",
        )
        aiff = _item(output_format="aiff")
        aiff.source_el.set("Name", "New")
        self.assertEqual(
            classify_assignment(
                item=aiff,
                record=_complete_record(_item(output_format="aiff")),
                force=False,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "update_metadata",
        )
        self.assertEqual(
            classify_assignment(
                item=_item(),
                record=_complete_record(_item()),
                force=False,
                dest_exists=True,
                dest_stat={"size": 20, "mtime_ns": 2},
                source_stat={"size": 10, "mtime_ns": 1},
            ),
            "reuse",
        )


if __name__ == "__main__":
    unittest.main()
