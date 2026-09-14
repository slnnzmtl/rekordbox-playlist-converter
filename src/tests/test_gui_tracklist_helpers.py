#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gui.tracklist import track_preview_row


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
