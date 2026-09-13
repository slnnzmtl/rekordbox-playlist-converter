#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_SRC, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import rb_playlist_to_wav as rb
import convert_plan
from convert import encode
import ffmpeg_tools
from convert_fixtures import HangProc as _HangProc


class SubprocessTimeoutTests(unittest.TestCase):
    def test_run_ffprobe_maps_timeout_to_cli_error(self) -> None:
        path = Path("/tmp/track.flac")

        with patch.object(
            ffmpeg_tools, "tool_path", return_value="/bin/ffprobe"
        ), patch.object(
            ffmpeg_tools.subprocess, "Popen", _HangProc
        ), patch.object(
            ffmpeg_tools.time, "monotonic", side_effect=[0.0, 100.0]
        ), patch.object(ffmpeg_tools.time, "sleep", lambda _s: None):
            with self.assertRaises(rb.CliError) as ctx:
                ffmpeg_tools.run_ffprobe(path)
        self.assertIn("timed out", str(ctx.exception).lower())
        self.assertIn(str(path), str(ctx.exception))

    def test_run_ffmpeg_timeout_scales_with_convert_workers(self) -> None:
        """Given CONVERT_WORKERS=4 and FFMPEG_CONVERT_TIMEOUT_S=100: When
        ffmpeg hangs past the per-worker budget: Then the CliError deadline is
        400s (timeout * workers), not the unscaled 100s."""
        communicated: list[bool] = []

        class FakeProc(_HangProc):
            def communicate(self) -> tuple[str, str]:
                communicated.append(True)
                return super().communicate()

        src = Path("/tmp/src.flac")
        dest = Path("/tmp/out.wav")
        with patch.object(
            ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
        ), patch.object(
            ffmpeg_tools, "ffmpeg_supports_soxr", return_value=False
        ), patch.object(
            ffmpeg_tools, "FFMPEG_CONVERT_TIMEOUT_S", 100
        ), patch.object(
            convert_plan, "CONVERT_WORKERS", 4
        ), patch.object(encode.subprocess, "Popen", FakeProc), patch.object(
            ffmpeg_tools.time, "sleep", lambda _s: None
        ), patch.object(
            ffmpeg_tools.time, "monotonic", side_effect=[0.0, 401.0]
        ):
            with self.assertRaises(rb.CliError) as ctx:
                convert_plan.run_ffmpeg(src, dest, "pcm_s16le", force=True)
        msg = str(ctx.exception).lower()
        self.assertIn("timed out", msg)
        self.assertIn("400", msg, f"deadline must scale to 100*4=400s; got {msg!r}")
        self.assertNotIn(
            "after 100s",
            msg,
            "must not report the unscaled per-process timeout alone",
        )
        self.assertTrue(communicated)

    def test_extract_cover_jpeg_returns_none_on_timeout(self) -> None:
        import cdj_aiff

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "song.aiff"
            src.write_bytes(b"x")

            with patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
            ), patch.object(cdj_aiff.subprocess, "Popen", _HangProc), patch.object(
                ffmpeg_tools.time, "monotonic", side_effect=[0.0, 100.0]
            ), patch.object(ffmpeg_tools.time, "sleep", lambda _s: None):
                self.assertIsNone(rb.extract_cover_jpeg(src))

    def test_ffmpeg_supports_soxr_false_on_timeout(self) -> None:
        import subprocess

        ffmpeg_tools.ffmpeg_supports_soxr.cache_clear()
        try:
            with patch.object(
                ffmpeg_tools, "tool_path", return_value="/bin/ffmpeg"
            ), patch.object(
                ffmpeg_tools.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=15),
            ):
                self.assertFalse(ffmpeg_tools.ffmpeg_supports_soxr())
        finally:
            ffmpeg_tools.ffmpeg_supports_soxr.cache_clear()


if __name__ == "__main__":
    unittest.main()
