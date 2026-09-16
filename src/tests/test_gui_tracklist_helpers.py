#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gui.tracklist import (
    TRACKLIST_TWISTY_CLOSED,
    TRACKLIST_TWISTY_OPEN,
    TRACKLIST_VALUE_COLUMNS,
    order_tracklist_leaves,
    track_preview_row,
    tracklist_display_column_id,
    tracklist_display_columns,
    tracklist_group_values,
    tracklist_sort_key,
)


class TracklistColumnLayoutTests(unittest.TestCase):
    def test_twisty_follows_index_and_group_title_uses_track(self) -> None:
        """Given value columns: When building a group header: Then twisty sits
        after index and the playlist title lands in track."""
        self.assertEqual(
            TRACKLIST_VALUE_COLUMNS,
            (
                "missing",
                "index",
                "twisty",
                "track",
                "format",
                "bit_depth",
                "sample_rate",
                "rating",
            ),
        )
        open_values = tracklist_group_values("Dark forest (3 tracks)", is_open=True)
        self.assertEqual(open_values[1], "")
        self.assertEqual(open_values[2], TRACKLIST_TWISTY_OPEN)
        self.assertEqual(open_values[3], "Dark forest (3 tracks)")
        closed = tracklist_group_values("Dark forest (3 tracks)", is_open=False)
        self.assertEqual(closed[2], TRACKLIST_TWISTY_CLOSED)

    def test_display_columns_omit_missing_when_unused(self) -> None:
        """Given no missing marks: When choosing display columns: Then missing
        is omitted; when marks exist it is included first."""
        self.assertEqual(
            tracklist_display_columns(show_missing=False),
            (
                "index",
                "twisty",
                "track",
                "format",
                "bit_depth",
                "sample_rate",
                "rating",
            ),
        )
        self.assertEqual(
            tracklist_display_columns(show_missing=True),
            TRACKLIST_VALUE_COLUMNS,
        )

    def test_display_column_id_maps_identify_token(self) -> None:
        """Given displaycolumns without missing: When mapping #2: Then twisty."""
        display = tracklist_display_columns(show_missing=False)
        self.assertEqual(tracklist_display_column_id("#1", display), "index")
        self.assertEqual(tracklist_display_column_id("#2", display), "twisty")
        self.assertIsNone(tracklist_display_column_id("#0", display))


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
            "a": ("", ["", "10", "", "Track A", "FLAC", "—", "44100", "☆☆☆☆☆"]),
            "b": ("", ["", "2", "", "Track B", "AIFF", "—", "48000", "☆☆☆☆☆"]),
        }

        def sort_key(iid: str, column: str):
            text, values = rows[iid]
            return tracklist_sort_key(text, values, column)

        ordered = order_tracklist_leaves(
            ["a", "b"],
            column="index",
            reverse=False,
            sort_key=sort_key,
        )
        self.assertEqual(ordered, ["b", "a"])

    def test_missing_column_sorts_present_then_missing_lexically(self) -> None:
        """Given present and missing status cells: When sorting by missing:
        Then present rows come before !; reverse flips that order."""
        rows = {
            "present": ("", ["", "1", "", "Present", "FLAC", "—", "44100", "☆☆☆☆☆"]),
            "missing": ("", ["!", "2", "", "Gone", "—", "—", "—", "—"]),
        }

        def sort_key(iid: str, column: str):
            text, values = rows[iid]
            return tracklist_sort_key(text, values, column)

        ascending = order_tracklist_leaves(
            ["missing", "present"],
            column="missing",
            reverse=False,
            sort_key=sort_key,
        )
        self.assertEqual(ascending, ["present", "missing"])
        descending = order_tracklist_leaves(
            ["present", "missing"],
            column="missing",
            reverse=True,
            sort_key=sort_key,
        )
        self.assertEqual(descending, ["missing", "present"])
