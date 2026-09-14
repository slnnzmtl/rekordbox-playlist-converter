"""Source, metadata, output, and recipe freshness for manifest v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def source_signature(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "hash": None}
