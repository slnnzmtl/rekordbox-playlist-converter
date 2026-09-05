#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import rb_playlist_to_wav as rb
from rb_converter_gui import total_successful_conversions


class TotalSuccessfulConversionsTests(unittest.TestCase):
    def test_total_successful_conversions_counts_converted(self) -> None:
        self.assertEqual(total_successful_conversions([rb.ConvertStats(converted=3)]), 3)

    def test_total_successful_conversions_counts_copied(self) -> None:
        self.assertEqual(total_successful_conversions([rb.ConvertStats(copied=2)]), 2)

    def test_total_successful_conversions_sums_converted_and_copied(self) -> None:
        self.assertEqual(
            total_successful_conversions([rb.ConvertStats(converted=1, copied=2)]), 3
        )

    def test_total_successful_conversions_ignores_skipped_and_appended(self) -> None:
        self.assertEqual(
            total_successful_conversions(
                [rb.ConvertStats(converted=0, copied=0, skipped=5, appended=10)]
            ),
            0,
        )

    def test_total_successful_conversions_sums_across_multiple_stats(self) -> None:
        self.assertEqual(
            total_successful_conversions(
                [
                    rb.ConvertStats(converted=1),
                    rb.ConvertStats(copied=2),
                    rb.ConvertStats(skipped=9),
                ]
            ),
            3,
        )

    def test_total_successful_conversions_empty_list(self) -> None:
        self.assertEqual(total_successful_conversions([]), 0)


if __name__ == "__main__":
    unittest.main()
