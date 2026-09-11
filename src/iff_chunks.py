"""Shared RIFF/AIFF-style chunk walking (even-padded payloads)."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import Literal


def iter_chunks(
    data: bytes,
    *,
    endian: Literal["little", "big"] = "little",
    start: int = 12,
) -> Iterator[tuple[bytes, int, int]]:
    """Yield ``(cid, size, payload_offset)`` for each chunk starting at *start*.

    Advances past even padding after each payload. Stops when fewer than 8
    header bytes remain. Does not validate declared container size or trailing
    bytes — callers that need strictness check those outside the loop.
    Raises ``ValueError`` if a chunk payload would extend past *data*.
    """
    fmt = "<I" if endian == "little" else ">I"
    offset = start
    while offset + 8 <= len(data):
        cid = data[offset : offset + 4]
        size = struct.unpack_from(fmt, data, offset + 4)[0]
        payload_offset = offset + 8
        payload_end = payload_offset + size
        if payload_end > len(data):
            raise ValueError(f"truncated chunk {cid!r}")
        yield cid, size, payload_offset
        offset = payload_end + (size % 2)
