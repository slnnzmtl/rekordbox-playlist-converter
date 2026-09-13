"""Test helper: wrap Path.open so reads on one path are counted."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock


class CountingFile:
    def __init__(self, fp: Any, counter: dict[str, int]) -> None:
        self._fp = fp
        self._counter = counter

    def read(self, size: int = -1) -> bytes:
        data = self._fp.read(size)
        self._counter["n"] += len(data)
        return data

    def seek(self, *args: Any, **kwargs: Any) -> Any:
        return self._fp.seek(*args, **kwargs)

    def tell(self) -> int:
        return self._fp.tell()

    def __enter__(self) -> CountingFile:
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._fp.__exit__(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fp, name)


def patch_counting_open(
    path: Path, bytes_read: dict[str, int]
) -> tuple[Any, Any]:
    """Return (open_patch, read_bytes_patch) that count reads on *path* only."""
    real_open = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self != path:
            return real_open(self, *args, **kwargs)
        return CountingFile(real_open(self, *args, **kwargs), bytes_read)

    open_patch = mock.patch.object(Path, "open", counting_open)
    read_bytes_patch = mock.patch.object(
        Path, "read_bytes", side_effect=AssertionError("must not read_bytes")
    )
    return open_patch, read_bytes_patch
