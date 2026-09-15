#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_SRC, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import converter_manifest
from convert.freshness import bind_complete_assignment, metadata_signature, recipe_from_item
from convert.models import Plan, PlannedTrack
from convert.paths import source_key
from convert.rerun import classify_assignment, classify_item
from convert_fixtures import write_pcm_wav


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
            ).action,
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
            ).action,
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
            ).action,
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
            classify_assignment(**kwargs, force=False).action,
            "external_modification_conflict",
        )
        self.assertEqual(
            classify_assignment(**kwargs, force=True).action,
            "external_modification_conflict",
        )

    def test_incomplete_dest_mismatch_is_rebuild_not_conflict(self) -> None:
        """Given an incomplete record whose dest stats differ: When classify:
        Then rebuild instead of treating the dest as an external edit."""
        item = _item()
        record = _complete_record(item)
        record["state"] = "incomplete"
        self.assertEqual(
            classify_assignment(
                item=item,
                record=record,
                force=False,
                dest_exists=True,
                dest_stat={"size": 99, "mtime_ns": 9},
                source_stat={"size": 10, "mtime_ns": 1},
            ).action,
            "transcode",
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
            ).action,
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
            ).action,
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
            ).action,
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
            ).action,
            "transcode",
        )

    def test_source_recipe_and_metadata_changes(self) -> None:
        """Given complete records: When source, recipe, or metadata differ: Then
        the matching rebuild or refresh action and reason code are chosen."""
        item = _item()
        record = _complete_record(item)
        source_changed = classify_assignment(
            item=item,
            record=record,
            force=False,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 11, "mtime_ns": 1},
        )
        self.assertEqual(source_changed.action, "transcode")
        self.assertEqual(source_changed.reason, "source_changed")
        self.assertEqual(source_changed.write_kind, "audio")

        deeper = _item(bit_depth=16, sample_rate=44100)
        recipe_changed = classify_assignment(
            item=deeper,
            record=_complete_record(_item()),
            force=False,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(recipe_changed.action, "transcode")
        self.assertEqual(recipe_changed.reason, "recipe_changed")

        revision = classify_assignment(
            item=item,
            record=_complete_record(item, revision=2),
            force=False,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(revision.action, "transcode")
        self.assertEqual(revision.reason, "revision_changed")

        passthrough_rev = classify_assignment(
            item=_item(passthrough=True),
            record=_complete_record(_item(passthrough=True), revision=2),
            force=False,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(passthrough_rev.action, "rewrite_container")
        self.assertEqual(passthrough_rev.reason, "revision_changed")

        wav = _item()
        wav.source_el.set("Name", "New")
        refresh = classify_assignment(
            item=wav,
            record=_complete_record(_item()),
            force=False,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(refresh.action, "refresh_xml")
        self.assertEqual(refresh.reason, "metadata_changed")
        self.assertEqual(refresh.write_kind, "metadata")

        aiff = _item(output_format="aiff")
        aiff.source_el.set("Name", "New")
        update = classify_assignment(
            item=aiff,
            record=_complete_record(_item(output_format="aiff")),
            force=False,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(update.action, "update_metadata")
        self.assertEqual(update.reason, "metadata_changed")

        reuse = classify_assignment(
            item=_item(),
            record=_complete_record(_item()),
            force=False,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(reuse.action, "reuse")
        self.assertEqual(reuse.reason, "unchanged")
        self.assertEqual(reuse.write_kind, "none")

    def test_force_incomplete_unverified_reasons(self) -> None:
        """Force, incomplete, and unverified each expose a distinct reason."""
        item = _item()
        forced = classify_assignment(
            item=item,
            record=_complete_record(item),
            force=True,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(forced.action, "transcode")
        self.assertEqual(forced.reason, "force")

        incomplete = _complete_record(item)
        incomplete["state"] = "incomplete"
        inc = classify_assignment(
            item=item,
            record=incomplete,
            force=False,
            dest_exists=True,
            dest_stat={"size": 99, "mtime_ns": 9},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(inc.action, "transcode")
        self.assertEqual(inc.reason, "incomplete")

        unverified = classify_assignment(
            item=item,
            record={"dest": "WAV/A.wav"},
            force=False,
            dest_exists=True,
            dest_stat={"size": 20, "mtime_ns": 2},
            source_stat={"size": 10, "mtime_ns": 1},
        )
        self.assertEqual(unverified.action, "transcode")
        self.assertEqual(unverified.reason, "unverified")


class ClassifyItemTests(unittest.TestCase):
    def test_fresh_dest_mtime_flips_reuse_to_conflict(self) -> None:
        """Given a complete matching dest: When dest mtime changes: Then
        classify_item returns external_modification_conflict."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.flac"
            src.write_bytes(b"fLaC")
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(dest)
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location="file://localhost/A",
                dest_name=dest.name,
                codec=None,
                passthrough=False,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
                manifest=converter_manifest.empty_manifest(),
            )
            bind_complete_assignment(plan.manifest, item, "WAV/A.wav")
            self.assertEqual(classify_item(plan, item, False).action, "reuse")
            st = dest.stat()
            os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            self.assertEqual(
                classify_item(plan, item, False).action,
                "external_modification_conflict",
            )

    def test_revision_on_unrewriteable_dest_rewrites_from_safe_source(self) -> None:
        """Given a complete passthrough dest that is not a valid WAV: When the
        recipe revision changes: Then classify_item chooses rewrite_container
        so PCM is taken from the safe source."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.wav"
            dest = root / "WAV" / "A.wav"
            dest.parent.mkdir()
            write_pcm_wav(src, frames=8)
            dest.write_bytes(b"not a wav")
            el = ET.Element("TRACK", {"Name": "Song", "Artist": "DJ"})
            item = PlannedTrack(
                source_el=el,
                source_path=src,
                dest_path=dest,
                dest_location="file://localhost/A",
                dest_name=dest.name,
                codec=None,
                passthrough=True,
                noop=False,
                bit_depth=16,
                sample_rate=44100,
                output_format="wav",
            )
            plan = Plan(
                playlist_name="P",
                wav_playlist_name="P [WAV]",
                library_dir=root,
                media_dir=dest.parent,
                output=root / "o.xml",
                tracks=[item],
                unique=[item],
                source_root=ET.Element("DJ_PLAYLISTS"),
                output_root=ET.Element("DJ_PLAYLISTS"),
                output_existed=False,
                manifest=converter_manifest.empty_manifest(),
            )
            bind_complete_assignment(plan.manifest, item, "WAV/A.wav")
            plan.manifest.tracks[source_key(src)]["wav"]["recipe"]["revision"] = 2
            self.assertEqual(
                classify_item(plan, item, False).action, "rewrite_container"
            )


class FileSnapshotTests(unittest.TestCase):
    def test_file_snapshot_matches_size_and_mtime(self) -> None:
        """Given a file: When snapshot is taken twice after an explicit mtime
        bump: Then snapshots_match is false even if size is unchanged."""
        from convert.rerun import file_snapshot, snapshots_match

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.wav"
            path.write_bytes(b"abcd")
            first = file_snapshot(path)
            assert first is not None
            self.assertTrue(snapshots_match(first, file_snapshot(path)))
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
            second = file_snapshot(path)
            self.assertFalse(snapshots_match(first, second))
            self.assertEqual(first["size"], second["size"])


if __name__ == "__main__":
    unittest.main()
