"""Shared output-format and quality allowlists (parse vs coerce)."""

from __future__ import annotations

OUTPUT_FORMATS = frozenset({"wav", "aiff"})
BIT_DEPTHS = frozenset({16, 24})
SAMPLE_RATES = frozenset({44100, 48000})

DEFAULT_OUTPUT_FORMAT = "wav"
DEFAULT_BIT_DEPTH = 24
DEFAULT_SAMPLE_RATE = 48000

FORMAT_DIR_NAMES = {"wav": "WAV", "aiff": "AIFF"}


def parse_output_format(value: object) -> str | None:
    """Return wav|aiff when value is an allowed format; otherwise None."""
    if not isinstance(value, str):
        return None
    fmt = value.strip().lower()
    if fmt in OUTPUT_FORMATS:
        return fmt
    return None


def parse_bit_depth(value: object) -> int | None:
    """Return 16 or 24 when value is an allowed depth; otherwise None."""
    if value in BIT_DEPTHS:
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text in ("16", "24"):
            return int(text)
    return None


def parse_sample_rate(value: object) -> int | None:
    """Return 44100 or 48000 when value is an allowed rate; otherwise None."""
    if value in SAMPLE_RATES:
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text in ("44100", "48000"):
            return int(text)
    return None


def require_output_format(value: object) -> str:
    """Return an allowed format or raise CliError."""
    from cli_error import CliError

    parsed = parse_output_format(value)
    if parsed is None:
        raise CliError(f"unsupported output format: {value!r}")
    return parsed


def require_bit_depth(value: object) -> int:
    """Return an allowed bit depth or raise CliError."""
    from cli_error import CliError

    parsed = parse_bit_depth(value)
    if parsed is None:
        raise CliError(f"unsupported bit depth: {value!r}")
    return parsed


def require_sample_rate(value: object) -> int:
    """Return an allowed sample rate or raise CliError."""
    from cli_error import CliError

    parsed = parse_sample_rate(value)
    if parsed is None:
        raise CliError(f"unsupported sample rate: {value!r}")
    return parsed


def coerce_output_format(value: object) -> str:
    """Return an allowed format, defaulting to wav."""
    return parse_output_format(value) or DEFAULT_OUTPUT_FORMAT


def coerce_bit_depth(value: object) -> int:
    """Return an allowed bit depth, defaulting to 24."""
    return parse_bit_depth(value) or DEFAULT_BIT_DEPTH


def coerce_sample_rate(value: object) -> int:
    """Return an allowed sample rate, defaulting to 48000."""
    return parse_sample_rate(value) or DEFAULT_SAMPLE_RATE
