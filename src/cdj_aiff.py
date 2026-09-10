"""Pioneer-ceiling AIFF parse, ID3v2.3 tagging, and canonical validation."""

from __future__ import annotations

import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from cdj_wav import CDJ_SAFE_CHANNELS
from cli_error import CliError

AIFF_SAFE_RATES = {
    bytes.fromhex("400eac44000000000000"),  # 44100
    bytes.fromhex("400ebb80000000000000"),  # 48000
}
AIFF_RATE_BYTES = {
    44100: bytes.fromhex("400eac44000000000000"),
    48000: bytes.fromhex("400ebb80000000000000"),
}
AIFF_SAFE_BIT_DEPTHS = {16, 24}


@dataclass(frozen=True)
class _AiffAudioInfo:
    channels: int
    sample_frames: int
    bits_per_sample: int
    sample_rate_bytes: bytes
    chunk_ids: tuple[str, ...]
    comm_count: int
    ssnd_count: int
    id3_count: int
    ssnd_data_size: int
    ssnd_offset: int
    ssnd_block_size: int
    form_ok: bool


def _parse_aiff_audio(path: Path) -> _AiffAudioInfo:
    """Parse FORM/AIFF structure for Pioneer-ceiling safety checks."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read AIFF: {path}: {exc}") from exc
    if len(data) < 12 or data[0:4] != b"FORM":
        raise CliError(f"not a FORM file: {path}")
    declared = struct.unpack_from(">I", data, 4)[0]
    if declared + 8 != len(data):
        raise CliError(f"invalid FORM length in {path}")
    form_type = data[8:12]
    form_ok = form_type == b"AIFF"
    chunk_ids: list[str] = []
    channels = sample_frames = bits = 0
    sample_rate_bytes = b""
    comm_count = ssnd_count = id3_count = 0
    ssnd_data_size = ssnd_offset = ssnd_block_size = 0
    comm_size = 0
    offset = 12
    while offset + 8 <= len(data):
        cid = data[offset : offset + 4]
        size = struct.unpack_from(">I", data, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        if payload_end > len(data):
            raise CliError(f"truncated AIFF chunk {cid!r} in {path}")
        chunk_ids.append(cid.decode("ascii", errors="replace"))
        if cid == b"COMM":
            comm_count += 1
            comm_size = size
            if size >= 18:
                channels, sample_frames, bits = struct.unpack_from(
                    ">hIh", data, payload_start
                )
                sample_rate_bytes = data[payload_start + 8 : payload_start + 18]
        elif cid == b"SSND":
            ssnd_count += 1
            if size >= 8:
                ssnd_offset, ssnd_block_size = struct.unpack_from(
                    ">II", data, payload_start
                )
                ssnd_data_size = size - 8
        elif cid == b"ID3 ":
            id3_count += 1
        offset = payload_end + (size % 2)
    if offset != len(data):
        raise CliError(f"trailing bytes after AIFF chunks in {path}")
    if not form_ok:
        raise CliError(f"not uncompressed AIFF (got {form_type!r}): {path}")
    if comm_count != 1 or ssnd_count != 1:
        raise CliError(f"AIFF must have one COMM and one SSND: {path}")
    if id3_count > 1:
        raise CliError(f"AIFF has multiple ID3 chunks: {path}")
    if comm_size != 18:
        raise CliError(f"COMM chunk size must be 18 for PCM AIFF: {path}")
    expected_pcm = sample_frames * channels * (bits // 8) if bits % 8 == 0 else -1
    if expected_pcm < 0 or ssnd_data_size < expected_pcm:
        raise CliError(f"SSND payload shorter than COMM frame count: {path}")
    return _AiffAudioInfo(
        channels=channels,
        sample_frames=sample_frames,
        bits_per_sample=bits,
        sample_rate_bytes=sample_rate_bytes,
        chunk_ids=tuple(chunk_ids),
        comm_count=comm_count,
        ssnd_count=ssnd_count,
        id3_count=id3_count,
        ssnd_data_size=ssnd_data_size,
        ssnd_offset=ssnd_offset,
        ssnd_block_size=ssnd_block_size,
        form_ok=form_ok,
    )


def is_cdj_safe_aiff(
    path: Path,
    *,
    bit_depth: int = 16,
    sample_rate: int = 44100,
) -> bool:
    """True if path is stereo PCM AIFF at the given bit depth and sample rate.

    Harmless extra chunks such as NAME are allowed; AIFC is not.
    """
    if bit_depth not in AIFF_SAFE_BIT_DEPTHS:
        bit_depth = 16
    rate_bytes = AIFF_RATE_BYTES.get(sample_rate)
    if rate_bytes is None:
        rate_bytes = AIFF_RATE_BYTES[44100]
        bit_depth = 16
    if not path.is_file():
        return False
    try:
        info = _parse_aiff_audio(path)
    except CliError:
        return False
    return (
        info.form_ok
        and info.channels == CDJ_SAFE_CHANNELS
        and info.bits_per_sample == bit_depth
        and info.sample_rate_bytes == rate_bytes
        and info.comm_count == 1
        and info.ssnd_count == 1
        and info.id3_count <= 1
        and info.ssnd_offset == 0
    )


def expected_id3_text_from_track(source_el: ET.Element) -> dict[str, str]:
    """Map Rekordbox TRACK attributes to ID3v2.3 frame ids; omit empties."""
    mapping = (
        ("Name", "TIT2"),
        ("Artist", "TPE1"),
        ("Album", "TALB"),
        ("Genre", "TCON"),
        ("Year", "TYER"),
        ("TrackNumber", "TRCK"),
    )
    out: dict[str, str] = {}
    for attr, frame in mapping:
        value = (source_el.get(attr) or "").strip()
        if value:
            out[frame] = value
    return out


def _synchsafe(n: int) -> bytes:
    return bytes(
        [
            (n >> 21) & 0x7F,
            (n >> 14) & 0x7F,
            (n >> 7) & 0x7F,
            n & 0x7F,
        ]
    )


def _encode_id3_text(value: str) -> bytes:
    # Encoding 1 = UTF-16 with BOM (ID3v2.3 has no UTF-8).
    return b"\x01" + value.encode("utf-16")


def build_id3v23_tag(
    text_frames: dict[str, str], cover_jpeg: bytes | None
) -> bytes:
    """Build an ID3v2.3.0 tag body (including 'ID3' header) without footer/ext."""
    frames = b""
    for frame_id in ("TIT2", "TPE1", "TALB", "TCON", "TYER", "TRCK"):
        if frame_id not in text_frames:
            continue
        payload = _encode_id3_text(text_frames[frame_id])
        frames += frame_id.encode("ascii")
        frames += struct.pack(">I", len(payload))
        frames += b"\x00\x00"  # flags
        frames += payload
    if cover_jpeg is not None:
        # encoding + mime\0 + pic type + desc\0 + data
        mime = b"image/jpeg"
        # UTF-16 empty description: BOM + 0x0000
        desc = b"\xff\xfe\x00\x00"
        apic = b"\x01" + mime + b"\x00" + b"\x03" + desc + cover_jpeg
        frames += b"APIC"
        frames += struct.pack(">I", len(apic))
        frames += b"\x00\x00"
        frames += apic
    # flags: no unsync, no extended header, no experimental
    return b"ID3\x03\x00\x00" + _synchsafe(len(frames)) + frames


def _read_id3_frames(tag: bytes) -> tuple[dict[str, str], bytes | None]:
    """Parse ID3v2.3 text frames and optional JPEG APIC from a raw ID3 tag."""
    if len(tag) < 10 or tag[0:3] != b"ID3":
        return {}, None
    version = tag[3]
    if version != 3:
        return {}, None
    size = (
        ((tag[6] & 0x7F) << 21)
        | ((tag[7] & 0x7F) << 14)
        | ((tag[8] & 0x7F) << 7)
        | (tag[9] & 0x7F)
    )
    flags = tag[5]
    offset = 10
    if flags & 0x40:  # extended header — unsupported for canonical
        return {}, None
    end = 10 + size
    if end > len(tag):
        end = len(tag)
    text: dict[str, str] = {}
    cover: bytes | None = None
    while offset + 10 <= end:
        frame_id = tag[offset : offset + 4]
        if frame_id == b"\x00\x00\x00\x00":
            break
        frame_size = struct.unpack_from(">I", tag, offset + 4)[0]
        frame_flags = tag[offset + 8 : offset + 10]
        offset += 10
        if frame_size < 0 or offset + frame_size > end:
            break
        payload = tag[offset : offset + frame_size]
        offset += frame_size
        if frame_flags[1] & 0xC0:
            continue  # compression/encryption
        fid = frame_id.decode("ascii", errors="replace")
        if fid.startswith("T") and fid != "TXXX" and payload:
            enc = payload[0]
            raw = payload[1:]
            if enc == 0:
                value = raw.split(b"\x00", 1)[0].decode("latin-1", errors="replace")
            elif enc == 1:
                value = raw.decode("utf-16", errors="replace").rstrip("\x00")
            else:
                continue
            text[fid] = value
        elif fid == "APIC" and payload:
            enc = payload[0]
            rest = payload[1:]
            mime_end = rest.find(b"\x00")
            if mime_end < 0:
                continue
            mime = rest[:mime_end].decode("ascii", errors="replace")
            rest = rest[mime_end + 1 :]
            if not rest:
                continue
            pic_type = rest[0]
            rest = rest[1:]
            if enc == 1:
                # UTF-16 description terminated by 0x0000
                if len(rest) < 2:
                    continue
                # skip BOM+empty or find double NUL
                i = 0
                if rest.startswith(b"\xff\xfe") or rest.startswith(b"\xfe\xff"):
                    i = 2
                while i + 1 < len(rest):
                    if rest[i : i + 2] == b"\x00\x00":
                        i += 2
                        break
                    i += 2
                img = rest[i:]
            else:
                z = rest.find(b"\x00")
                if z < 0:
                    continue
                img = rest[z + 1 :]
            if mime == "image/jpeg" and pic_type == 0x03:
                cover = img
    return text, cover


def _extract_id3_chunk(path: Path) -> bytes | None:
    try:
        info = _parse_aiff_audio(path)
        data = path.read_bytes()
    except (CliError, OSError):
        return None
    offset = 12
    for cid in info.chunk_ids:
        size = struct.unpack_from(">I", data, offset + 4)[0]
        payload = data[offset + 8 : offset + 8 + size]
        if cid == "ID3 ":
            return payload
        offset += 8 + size + (size % 2)
    return None


def _ssnd_pcm_bytes(path: Path) -> bytes:
    info = _parse_aiff_audio(path)
    data = path.read_bytes()
    offset = 12
    for cid in info.chunk_ids:
        size = struct.unpack_from(">I", data, offset + 4)[0]
        if cid == "SSND":
            return data[offset + 16 : offset + 8 + size]
        offset += 8 + size + (size % 2)
    raise CliError(f"SSND missing in {path}")


def _normalize_aiff_audio_chunks(source: Path, dest: Path) -> None:
    """Copy COMM+SSND only from source into dest (SSND PCM bit-identical)."""
    info = _parse_aiff_audio(source)
    data = source.read_bytes()
    pieces: list[bytes] = []
    offset = 12
    for cid in info.chunk_ids:
        size = struct.unpack_from(">I", data, offset + 4)[0]
        chunk = data[offset : offset + 8 + size]
        if size % 2:
            # include pad if present in file
            if offset + 8 + size < len(data) and (offset + 8 + size) % 2:
                pass
        if cid in ("COMM", "SSND"):
            payload = data[offset + 8 : offset + 8 + size]
            pieces.append(cid.encode("ascii") + struct.pack(">I", len(payload)) + payload)
            if len(payload) % 2:
                pieces.append(b"\x00")
        offset += 8 + size + (size % 2)
    body = b"".join(pieces)
    form = b"AIFF" + body
    dest.write_bytes(b"FORM" + struct.pack(">I", len(form)) + form)


def write_aiff_id3(
    path: Path, source_el: ET.Element, cover_jpeg: bytes | None
) -> None:
    """Replace/add ID3 chunk; drop NAME and other non-COMM/SSND/ID3 chunks."""
    text = expected_id3_text_from_track(source_el)
    tag = build_id3v23_tag(text, cover_jpeg)
    info = _parse_aiff_audio(path)
    data = path.read_bytes()
    pieces: list[bytes] = []
    offset = 12
    for cid in info.chunk_ids:
        size = struct.unpack_from(">I", data, offset + 4)[0]
        payload = data[offset + 8 : offset + 8 + size]
        if cid in ("COMM", "SSND"):
            pieces.append(cid.encode("ascii") + struct.pack(">I", len(payload)) + payload)
            if len(payload) % 2:
                pieces.append(b"\x00")
        offset += 8 + size + (size % 2)
    pieces.append(b"ID3 " + struct.pack(">I", len(tag)) + tag)
    if len(tag) % 2:
        pieces.append(b"\x00")
    body = b"".join(pieces)
    form = b"AIFF" + body
    path.write_bytes(b"FORM" + struct.pack(">I", len(form)) + form)


def extract_cover_jpeg(source: Path, *, max_side: int = 600) -> bytes | None:
    """Extract attached picture as JPEG ≤ max_side; None if absent."""
    # Late import: tool_path lives on the facade; avoid import cycle at load time.
    from rb_playlist_to_wav import tool_path

    exe = tool_path("ffmpeg")
    if exe is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "cover.jpg"
        cmd = [
            exe,
            "-y",
            "-i",
            str(source),
            "-an",
            "-vf",
            f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease",
            "-frames:v",
            "1",
            str(out),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return None
        if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            return None
        data = out.read_bytes()
        if not data.startswith(b"\xff\xd8"):
            return None
        return data


def _is_canonical_aiff_output(
    path: Path,
    source_el: ET.Element,
    expected_cover: bytes | None,
    *,
    bit_depth: int = 16,
    sample_rate: int = 44100,
) -> bool:
    """True if dest is audio-safe with exactly COMM+SSND+ID3 matching XML+cover."""
    if not is_cdj_safe_aiff(path, bit_depth=bit_depth, sample_rate=sample_rate):
        return False
    try:
        info = _parse_aiff_audio(path)
    except CliError:
        return False
    if set(info.chunk_ids) != {"COMM", "SSND", "ID3 "} or info.id3_count != 1:
        return False
    if info.chunk_ids.count("COMM") != 1 or info.chunk_ids.count("SSND") != 1:
        return False
    tag = _extract_id3_chunk(path)
    if tag is None:
        return False
    text, cover = _read_id3_frames(tag)
    expected = expected_id3_text_from_track(source_el)
    if text != expected:
        return False
    if expected_cover is None:
        return cover is None
    return cover == expected_cover
