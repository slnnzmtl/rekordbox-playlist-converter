#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from convert.models import ConvertStats
from rb_converter_gui import (
    progress_action_status_hint,
    total_successful_conversions,
)


class TotalSuccessfulConversionsTests(unittest.TestCase):
    def test_total_successful_conversions(self) -> None:
        self.assertEqual(
            total_successful_conversions(
                [
                    ConvertStats(converted=1, copied=2),
                    ConvertStats(skipped=9, appended=10),
                ]
            ),
            3,
        )
        self.assertEqual(
            total_successful_conversions(
                [ConvertStats(pcm_rebuilt=2, metadata_refreshed=1)]
            ),
            3,
        )
        self.assertEqual(
            total_successful_conversions(
                [ConvertStats(converted=0, copied=0, skipped=5, appended=10)]
            ),
            0,
        )
        self.assertEqual(total_successful_conversions([]), 0)


class ProgressStatusHintTests(unittest.TestCase):
    def test_progress_action_status_hint_puts_counter_after_action(self) -> None:
        """Hint reads Convert (n/m) TrackName… so the counter stays visible."""
        self.assertEqual(
            progress_action_status_hint(
                "convert",
                357,
                1958,
                "0190 - Posij - Sun Tracker.wav",
            ),
            "Convert (357/1958) 0190 - Posij - Sun Tracker.wav…",
        )

    def test_progress_action_status_hint_import_xml_phase(self) -> None:
        """Finalize phase reads Updating import XML… (not Import_xml (n/n) …)."""
        self.assertEqual(
            progress_action_status_hint("import_xml", 1315, 1315, ""),
            "Updating import XML…",
        )


if __name__ == "__main__":
    unittest.main()
