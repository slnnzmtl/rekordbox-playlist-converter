#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gui.tracklist import order_tracklist_leaves, track_preview_row, tracklist_sort_key


class TrackPreviewRatingTests(unittest.TestCase):
    def test_track_preview_row_formats_rekordbox_star_rating(self) -> None:
        """Rating 0–255 maps to five stars; missing track and unrated stay clear."""
        cases = (
            ("255", "★★★★★"),
            ("204", "★★★★☆"),
            ("153", "★★★☆☆"),
            ("102", "★★☆☆☆"),
            ("51", "★☆☆☆☆"),
            ("0", "☆☆☆☆☆"),
            (None, "☆☆☆☆☆"),
            ("not-a-number", "☆☆☆☆☆"),
        )
        for raw, expected in cases:
            track = {"Name": "a"} if raw is None else {"Name": "a", "Rating": raw}
            with self.subTest(rating=raw):
                self.assertEqual(track_preview_row(track)[-1], expected)
        self.assertEqual(track_preview_row(None)[-1], "—")


class TracklistIndexSortTests(unittest.TestCase):
    def test_index_sort_is_numeric_not_lexical(self) -> None:
        """Given playlist indexes 2 and 10: When sorting by index ascending:
        Then 2 comes before 10 (numeric, not lexical)."""
        rows = {
            "a": ("10", ["Track A", "FLAC", "—", "44100", "☆☆☆☆☆"]),
            "b": ("2", ["Track B", "AIFF", "—", "48000", "☆☆☆☆☆"]),
        }

        def sort_key(iid: str, column: str):
            text, values = rows[iid]
            return tracklist_sort_key(text, values, column)

        ordered = order_tracklist_leaves(
            ["a", "b"],
            column="#0",
            reverse=False,
            sort_key=sort_key,
        )
        self.assertEqual(ordered, ["b", "a"])
