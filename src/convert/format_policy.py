"""Source classification, destination reuse policy, and in-place validation."""

from __future__ import annotations

import threading
from pathlib import Path

from cli_error import CliError
from convert.models import Plan, PlannedTrack
from convert.paths import same_file, target_from_stream
from convert.quality import coerce_output_format


def pcm_codec_for_depth(bit_depth: int, *, output_format: str = "wav") -> str:
    """Map effective bit depth to an ffmpeg PCM codec for the output container."""
    if coerce_output_format(output_format) == "aiff":
        return "pcm_s16be" if bit_depth == 16 else "pcm_s24be"
    return "pcm_s16le" if bit_depth == 16 else "pcm_s24le"


SUPPORTED_LOSSLESS_EXT = {".flac", ".aiff", ".aif", ".wav", ".wave", ".m4a", ".caf"}
WAV_EXT = {".wav", ".wave"}
ALAC_EXT = {".m4a", ".caf"}
AIFF_EXT = {".aiff", ".aif"}


def classify_source(
    path: Path,
    stream: dict,
    *,
    output_format: str = "wav",
    max_bit_depth: int = 24,
    max_sample_rate: int = 48000,
) -> tuple[str, bool, int, int]:
    """Return (ffmpeg_codec or 'copy', is_copy, bit_depth, sample_rate)."""
    output_format = coerce_output_format(output_format)
    ext = path.suffix.lower()
    codec_name = str(stream.get("codec_name") or "")
    if ext not in SUPPORTED_LOSSLESS_EXT:
        raise CliError(f"unsupported format: {path}")
    bits, rate = target_from_stream(
        stream, max_bit_depth=max_bit_depth, max_sample_rate=max_sample_rate
    )
    if ext in ALAC_EXT and codec_name != "alac":
        raise CliError(f"unsupported format: {path} (expected ALAC)")
    if output_format == "aiff":
        from cdj_aiff import is_cdj_safe_aiff

        if ext in AIFF_EXT and is_cdj_safe_aiff(
            path, bit_depth=bits, sample_rate=rate
        ):
            return "copy", True, bits, rate
        codec = pcm_codec_for_depth(bits, output_format="aiff")
        return codec, False, bits, rate
    if ext in ALAC_EXT:
        codec = pcm_codec_for_depth(bits, output_format="wav")
        return codec, False, bits, rate
    if ext in WAV_EXT:
        from cdj_wav import is_cdj_safe_wav

        if is_cdj_safe_wav(path, bit_depth=bits, sample_rate=rate):
            return "copy", True, bits, rate
        codec = pcm_codec_for_depth(bits, output_format="wav")
        return codec, False, bits, rate
    if ext in {".flac", ".aiff", ".aif"}:
        codec = pcm_codec_for_depth(bits, output_format="wav")
        return codec, False, bits, rate
    raise CliError(f"unsupported format: {path}")


def inplace_noop_and_error(
    item: PlannedTrack,
    *,
    output_format: str,
    is_copy: bool,
    cover: bytes | None,
    bit_depth: int,
    sample_rate: int,
) -> tuple[bool, str | None]:
    """Return (noop, error_message) for in-place source==dest cases during planning."""
    output_format = coerce_output_format(output_format)
    in_place = same_file(item.source_path, item.dest_path)
    if output_format == "aiff":
        if not in_place:
            return False, None
        from cdj_aiff import is_canonical_aiff_output

        if is_canonical_aiff_output(
            item.dest_path,
            item.source_el,
            cover,
            bit_depth=bit_depth,
            sample_rate=sample_rate,
        ):
            return True, None
        return False, (
            "refusing to convert in place "
            f"(source is not a canonical AIFF output): "
            f"{item.source_path}"
        )
    noop = is_copy and in_place
    if (not is_copy) and in_place:
        return False, (
            "refusing to convert in place "
            f"(source is not CDJ-safe WAV): {item.source_path}"
        )
    return noop, None


def planned_action(
    plan: Plan,
    item: PlannedTrack,
    force: bool,
    *,
    cover_lock: threading.Lock | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Classify read-only action from a fresh filesystem snapshot."""
    del cover_lock, cancel_event
    from convert.rerun import classify_item

    return classify_item(plan, item, force).action
