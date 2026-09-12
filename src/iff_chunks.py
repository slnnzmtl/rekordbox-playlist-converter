"""Shared RIFF/AIFF-style chunk walking (even-padded payloads)."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import BinaryIO, Literal


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


def iter_chunks_file(
    fp: BinaryIO,
    *,
    endian: Literal["little", "big"] = "little",
    start: int = 12,
    end: int | None = None,
) -> Iterator[tuple[bytes, int, int]]:
    """Yield ``(cid, size, payload_offset)`` by reading only 8-byte headers from *fp*.

    Does not load chunk payloads. If *end* is omitted, uses the current file
    size (seek to EOF). Raises ``ValueError`` if a payload would extend past *end*.
    """
    fmt = "<I" if endian == "little" else ">I"
    if end is None:
        pos = fp.tell()
        fp.seek(0, 2)
        end = fp.tell()
        fp.seek(pos)
    offset = start
    while offset + 8 <= end:
        fp.seek(offset)
        hdr = fp.read(8)
        if len(hdr) < 8:
            break
        cid = hdr[0:4]
        size = struct.unpack(fmt, hdr[4:8])[0]
        payload_offset = offset + 8
        payload_end = payload_offset + size
        if payload_end > end:
            raise ValueError(f"truncated chunk {cid!r}")
        yield cid, size, payload_offset
        offset = payload_end + (size % 2)


def copy_file_range(
    src: BinaryIO,
    dest: BinaryIO,
    start: int,
    size: int,
    *,
    bufsize: int = 1024 * 1024,
) -> None:
    """Copy *size* bytes from *src* at *start* to *dest* in chunks."""
    src.seek(start)
    remaining = size
    while remaining > 0:
        chunk = src.read(min(bufsize, remaining))
        if not chunk:
            raise ValueError("truncated stream copy")
        dest.write(chunk)
        remaining -= len(chunk)
