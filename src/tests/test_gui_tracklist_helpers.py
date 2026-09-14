#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gui.tracklist import order_tracklist_leaves, track_preview_row, tracklist_sort_key


def _rating(track) -> str:
    return track_preview_row(track)[-1]


class TrackPreviewRatingTests(unittest.TestCase):
    def test_track_preview_row_formats_rekordbox_star_rating(self) -> None:
        """Rating 0–255 maps to five stars; missing track and unrated stay clear."""
        self.assertEqual(_rating({"Name": "a", "Rating": "255"}), "★★★★★")
        self.assertEqual(_rating({"Name": "a", "Rating": "204"}), "★★★★☆")
        self.assertEqual(_rating({"Name": "a", "Rating": "153"}), "★★★☆☆")
        self.assertEqual(_rating({"Name": "a", "Rating": "102"}), "★★☆☆☆")
        self.assertEqual(_rating({"Name": "a", "Rating": "51"}), "★☆☆☆☆")
        self.assertEqual(_rating({"Name": "a", "Rating": "0"}), "☆☆☆☆☆")
        self.assertEqual(_rating({"Name": "a"}), "☆☆☆☆☆")
        self.assertEqual(_rating({"Name": "a", "Rating": "not-a-number"}), "☆☆☆☆☆")
        self.assertEqual(_rating(None), "—")

    def test_tracklist_sort_key_orders_star_ratings(self) -> None:
        """Filled-star count sorts ratings; missing stays after numbered rows."""
        self.assertEqual(
            tracklist_sort_key("", ["FLAC", "—", "44100", "★★★☆☆"], "rating"),
            (0, 3),
        )
        self.assertEqual(
            tracklist_sort_key("", ["FLAC", "—", "44100", "☆☆☆☆☆"], "rating"),
            (0, 0),
        )
        self.assertEqual(
            tracklist_sort_key("", ["—", "—", "—", "—"], "rating"),
            (1, 0),
        )

    def test_order_tracklist_leaves_sorts_rating_and_keeps_missing_last(self) -> None:
        keys = {
            "a": (0, 1),
            "b": (0, 4),
            "missing": (1, 0),
            "c": (0, 0),
        }
        ordered = order_tracklist_leaves(
            ["b", "missing", "a", "c"],
            column="rating",
            reverse=False,
            sort_key=lambda iid, _column: keys[iid],
        )
        self.assertEqual(ordered, ["c", "a", "b", "missing"])
        reversed_order = order_tracklist_leaves(
            ["b", "missing", "a", "c"],
            column="rating",
            reverse=True,
            sort_key=lambda iid, _column: keys[iid],
        )
        self.assertEqual(reversed_order, ["b", "a", "c", "missing"])

