"""Header-only bit depth for the GUI tracklist (FLAC, ALAC, WAV, AIFF)."""

from __future__ import annotations

import struct
from pathlib import Path

import iff_chunks

_FLAC_READ = 256
_RIFF_READ = 4096
_CONTAINER = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


def read_preview_bit_depth(path: Path) -> int | None:
    try:
        with path.open("rb") as fh:
            magic = fh.read(12)
            if magic.startswith(b"fLaC"):
                rest = fh.read(_FLAC_READ)
                return _flac_bit_depth(magic + rest)
            if len(magic) >= 12 and magic[0:4] == b"RIFF" and magic[8:12] == b"WAVE":
                rest = fh.read(_RIFF_READ)
                return _wav_bit_depth(magic + rest)
            if len(magic) >= 12 and magic[0:4] == b"FORM" and magic[8:12] in (
                b"AIFF",
                b"AIFC",
            ):
                rest = fh.read(_RIFF_READ)
                return _aiff_bit_depth(magic + rest)
            if len(magic) >= 8 and magic[4:8] in {b"ftyp", b"moov", b"mdat", b"free"}:
                fh.seek(0, 2)
                size = fh.tell()
                return _walk_mp4(fh, 0, size)
    except OSError:
        return None
    return None


def cached_preview_bit_depth(
    path: Path,
    cache: dict,
    *,
    read=read_preview_bit_depth,
    lock=None,
) -> int | None:
    """Return bit depth, reading the header only once per path+mtime+size."""
    key = _stat_cache_key(path)
    if key is None:
        return None
    if lock is None:
        if key in cache:
            return cache[key]
        bits = read(path)
        cache[key] = bits
        return bits
    with lock:
        if key in cache:
            return cache[key]
    bits = read(path)
    with lock:
        # Another worker may have filled it; prefer the first write.
        if key not in cache:
            cache[key] = bits
        return cache[key]


def peek_cached_preview_bit_depth(
    path: Path, cache: dict, *, lock=None
) -> tuple[bool, int | None]:
    """Return (hit, bits). Missing files and uncached paths are misses."""
    key = _stat_cache_key(path)
    if key is None:
        return False, None
    if lock is None:
        if key not in cache:
            return False, None
        return True, cache[key]
    with lock:
        if key not in cache:
            return False, None
        return True, cache[key]


def _stat_cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), st.st_mtime_ns, st.st_size)


def _flac_bit_depth(data: bytes) -> int | None:
    offset = 4
    while offset + 4 <= len(data):
        block_header = data[offset]
        block_type = block_header & 0x7F
        length = int.from_bytes(data[offset + 1 : offset + 4], "big")
        start = offset + 4
        end = start + length
        if end > len(data):
            return None
        if block_type == 0:
            if length < 18:
                return None
            packed = int.from_bytes(data[start + 10 : start + 18], "big")
            bits = ((packed >> 36) & 0x1F) + 1
            if 4 <= bits <= 32:
                return bits
            return None
        if block_header & 0x80:
            return None
        offset = end
    return None


def _wav_bit_depth(data: bytes) -> int | None:
    try:
        for cid, size, start in iff_chunks.iter_chunks(data, endian="little", start=12):
            if cid == b"fmt " and size >= 16:
                bits = struct.unpack_from("<H", data, start + 14)[0]
                if 8 <= bits <= 32:
                    return bits
                return None
    except ValueError:
        return None
    return None


def _aiff_bit_depth(data: bytes) -> int | None:
    try:
        for cid, size, start in iff_chunks.iter_chunks(data, endian="big", start=12):
            if cid == b"COMM" and size >= 8:
                bits = struct.unpack_from(">h", data, start + 6)[0]
                if 8 <= bits <= 32:
                    return bits
                return None
    except ValueError:
        return None
    return None


def _walk_mp4(fh, start: int, limit: int) -> int | None:
    pos = start
    while pos + 8 <= limit:
        fh.seek(pos)
        hdr = fh.read(16)
        if len(hdr) < 8:
            break
        size = struct.unpack_from(">I", hdr, 0)[0]
        typ = hdr[4:8]
        hdr_len = 8
        if size == 1:
            if len(hdr) < 16:
                break
            size = struct.unpack_from(">Q", hdr, 8)[0]
            hdr_len = 16
        elif size == 0:
            size = limit - pos
        if size < hdr_len:
            break
        box_end = pos + size
        if box_end > limit:
            break
        payload = pos + hdr_len
        found: int | None = None
        if typ in _CONTAINER:
            found = _walk_mp4(fh, payload, box_end)
        elif typ == b"stsd":
            if payload + 8 <= box_end:
                found = _walk_mp4(fh, payload + 8, box_end)
        elif typ == b"alac":
            payload_len = box_end - payload
            if payload_len >= 36:
                found = _walk_mp4(fh, payload + 28, box_end)
            if found is None and payload_len >= 6:
                fh.seek(payload)
                cfg = fh.read(6)
                if len(cfg) == 6:
                    bits = cfg[5]
                    if 8 <= bits <= 32:
                        found = bits
        if found is not None:
            return found
        pos = box_end
    return None
