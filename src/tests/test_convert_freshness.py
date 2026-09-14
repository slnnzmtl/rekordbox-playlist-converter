#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from convert.freshness import source_signature


class SourceSignatureTests(unittest.TestCase):
    def test_source_signature_records_size_and_mtime_without_writing(self) -> None:
        """Given a source file: When source_signature runs: Then size and
        mtime_ns match stat and the file bytes are unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.flac"
            path.write_bytes(b"fLaC-data")
            before = path.read_bytes()
            st = path.stat()
            sig = source_signature(path)
            self.assertEqual(sig["size"], st.st_size)
            self.assertEqual(sig["mtime_ns"], st.st_mtime_ns)
            self.assertIsNone(sig["hash"])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mtime_ns, st.st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
