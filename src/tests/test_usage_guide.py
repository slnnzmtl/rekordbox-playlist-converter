#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class GuiUsageGuideTests(unittest.TestCase):
    def test_usage_guide_covers_key_rekordbox_steps(self) -> None:
        import usage_guide

        text = usage_guide.USAGE_GUIDE
        self.assertIn("Imported Library", text)
        self.assertIn("File → Import", text)
        self.assertIn("Export BeatGrid", text)
        self.assertIn("do not use file → import", text.casefold())


if __name__ == "__main__":
    unittest.main()
