"""ffmpeg/ffprobe resolution and probing (no cdj_* imports)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from cli_error import CliError

FFPROBE_TIMEOUT_S = 60
FFMPEG_SOXR_TIMEOUT_S = 15
FFMPEG_CONVERT_TIMEOUT_S = 600
FFMPEG_COVER_TIMEOUT_S = 30

CODEC_BY_DEPTH = {
    16: "pcm_s16le",
    24: "pcm_s24le",
    32: "pcm_s32le",
}


def tool_path(name: str) -> str | None:
    """Resolve ffmpeg/ffprobe: bundled when frozen, else PATH."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / name
            if bundled.is_file():
                return str(bundled)
        beside = Path(sys.executable).resolve().parent / name
        if beside.is_file():
            return str(beside)
    return shutil.which(name)


def require_tools() -> list[str]:
    missing = []
    for name in ("ffmpeg", "ffprobe"):
        if tool_path(name) is None:
            missing.append(
                f"{name} not found on PATH (install with: brew install ffmpeg)"
            )
    return missing


def run_ffprobe(path: Path) -> dict:
    exe = tool_path("ffprobe")
    if exe is None:
        raise CliError(
            "ffprobe not found on PATH (install with: brew install ffmpeg)"
        )
    cmd = [
        exe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_fmt,sample_rate,channels,bits_per_raw_sample",
        "-show_entries",
        "format=format_name,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=FFPROBE_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise CliError(
            "ffprobe not found on PATH (install with: brew install ffmpeg)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CliError(
            f"ffprobe timed out after {FFPROBE_TIMEOUT_S}s for {path}"
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise CliError(f"ffprobe failed for {path}: {err}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise CliError(f"ffprobe returned invalid JSON for {path}") from exc


def first_stream(probe: dict) -> dict | None:
    streams = probe.get("streams") or []
    return streams[0] if streams else None


def pcm_codec_for_stream(stream: dict) -> str:
    fmt = str(stream.get("sample_fmt") or "")
    if fmt in ("flt", "fltp"):
        return "pcm_f32le"
    raw = stream.get("bits_per_raw_sample")
    bits: int | None = None
    if raw not in (None, "", "0", "N/A"):
        try:
            bits = int(raw)
        except (TypeError, ValueError):
            bits = None
    if bits is None and fmt in ("s16", "s16p"):
        bits = 16
    if bits is None:
        raise CliError("unknown bit depth")
    codec = CODEC_BY_DEPTH.get(bits)
    if codec is None:
        raise CliError(f"unknown bit depth ({bits})")
    return codec


def bit_depth_of_codec(codec: str) -> int:
    if codec in ("pcm_s16le", "pcm_s16be"):
        return 16
    if codec in ("pcm_s24le", "pcm_s24be"):
        return 24
    if codec in ("pcm_s32le", "pcm_f32le"):
        return 32
    raise CliError(f"unknown codec {codec}")


@lru_cache(maxsize=1)
def ffmpeg_supports_soxr() -> bool:
    exe = tool_path("ffmpeg")
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
            timeout=FFMPEG_SOXR_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    text = (proc.stdout or "") + (proc.stderr or "")
    return "soxr" in text.lower()
