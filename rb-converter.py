#!/usr/bin/env python3
"""Thin launcher so ./rb-converter.py works from the repo root."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from rb_playlist_to_wav import main

if __name__ == "__main__":
    sys.exit(main())
