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

import convert_plan
import ffmpeg_tools
import rb_playlist_to_wav as rb
from convert_fixtures import write_flac


class WizardHelperTests(unittest.TestCase):
    def test_main_requires_flags_when_non_tty(self) -> None:
        with patch.object(rb.sys.stdin, "isatty", return_value=False):
            rc = rb.main([])
        self.assertEqual(rc, 2)

    def test_tool_path_uses_path_when_not_frozen(self) -> None:
        with patch.object(ffmpeg_tools.sys, "frozen", False, create=True), patch.object(
            ffmpeg_tools.shutil, "which", return_value="/usr/local/bin/ffmpeg"
        ) as which:
            self.assertEqual(ffmpeg_tools.tool_path("ffmpeg"), "/usr/local/bin/ffmpeg")
            which.assert_called_once_with("ffmpeg")

    def test_tool_path_prefers_meipass_when_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meipass = Path(tmp)
            bundled = meipass / "ffmpeg"
            bundled.write_text("")
            bundled.chmod(0o755)
            with patch.object(ffmpeg_tools.sys, "frozen", True, create=True), patch.object(
                ffmpeg_tools.sys, "_MEIPASS", str(meipass), create=True
            ), patch.object(ffmpeg_tools.shutil, "which") as which:
                self.assertEqual(ffmpeg_tools.tool_path("ffmpeg"), str(bundled))
                which.assert_not_called()

    def test_tool_path_falls_back_to_executable_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mac_os = Path(tmp) / "MacOS"
            mac_os.mkdir()
            beside = mac_os / "ffprobe"
            beside.write_text("")
            beside.chmod(0o755)
            fake_exe = mac_os / "Simple Rekordbox Converter"
            fake_exe.write_text("")
            with patch.object(ffmpeg_tools.sys, "frozen", True, create=True), patch.object(
                ffmpeg_tools.sys, "_MEIPASS", str(Path(tmp) / "missing"), create=True
            ), patch.object(ffmpeg_tools.sys, "executable", str(fake_exe)), patch.object(
                ffmpeg_tools.shutil, "which"
            ) as which:
                self.assertEqual(
                    ffmpeg_tools.tool_path("ffprobe"),
                    str(beside.resolve()),
                )
                which.assert_not_called()

class TargetFromStreamTests(unittest.TestCase):
    """Quality is a ceiling: never raise depth or rate above the source."""

    def test_ceiling_table_from_issue(self) -> None:
        cases = [
            # source bits/rate, max bits/rate, expected bits/rate
            (16, 44100, 24, 48000, 16, 44100),
            (24, 44100, 24, 48000, 24, 44100),
            (16, 48000, 24, 48000, 16, 48000),
            (24, 48000, 16, 44100, 16, 44100),
            (24, 96000, 24, 48000, 24, 48000),
            (24, 88200, 24, 48000, 24, 44100),
            (24, 176400, 24, 48000, 24, 44100),
            (16, 22050, 24, 48000, 16, 44100),
        ]
        for src_bits, src_rate, max_bits, max_rate, exp_bits, exp_rate in cases:
            with self.subTest(
                src=(src_bits, src_rate), max_=(max_bits, max_rate)
            ):
                stream = {
                    "sample_fmt": f"s{src_bits}",
                    "bits_per_raw_sample": str(src_bits),
                    "sample_rate": str(src_rate),
                }
                bits, rate = rb.target_from_stream(
                    stream, max_bit_depth=max_bits, max_sample_rate=max_rate
                )
                self.assertEqual((bits, rate), (exp_bits, exp_rate))

    def test_invalid_ceilings_coerce_then_never_upconvert(self) -> None:
        """Given unsupported max_bit_depth/max_sample_rate: When
        target_from_stream runs: Then ceilings coerce to 24/48000 and
        source quality is still never raised."""
        stream = {
            "sample_fmt": "s16",
            "bits_per_raw_sample": "16",
            "sample_rate": "44100",
        }
        bits, rate = rb.target_from_stream(
            stream, max_bit_depth=32, max_sample_rate=96000
        )
        self.assertEqual((bits, rate), (16, 44100))

class PlanQualityFieldsTests(unittest.TestCase):
    def test_build_plan_stores_selected_and_effective_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "hi.flac"
            write_flac(src)
            xml_path = root / "c.xml"
            xml_path.write_text(
                f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Hi" Location="{rb.encode_location(src)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="P" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
""",
                encoding="utf-8",
            )
            with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
                ffmpeg_tools,
                "run_ffprobe",
                return_value={
                    "streams": [
                        {
                            "codec_name": "flac",
                            "sample_fmt": "s32",
                            "bits_per_raw_sample": "24",
                            "sample_rate": "96000",
                            "channels": "2",
                        }
                    ]
                },
            ):
                plan, errors = rb.prepare(
                    xml_path,
                    "P",
                    root / "out",
                    root / "import.xml",
                    output_format="wav",
                    max_bit_depth=24,
                    max_sample_rate=48000,
                )
            self.assertEqual(errors, [])
            assert plan is not None
            self.assertEqual(plan.output_format, "wav")
            self.assertEqual(plan.max_bit_depth, 24)
            self.assertEqual(plan.max_sample_rate, 48000)
            self.assertEqual(len(plan.unique), 1)
            self.assertEqual(plan.unique[0].bit_depth, 24)
            self.assertEqual(plan.unique[0].sample_rate, 48000)
            self.assertEqual(plan.unique[0].codec, "pcm_s24le")

    def test_prepare_classify_cancel_is_not_a_plan_error(self) -> None:
        """Given classify_source raises CancelledError: When prepare probes:
        Then CancelledError propagates instead of becoming a CliError string."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "hi.flac"
            write_flac(src)
            xml_path = root / "c.xml"
            xml_path.write_text(
                f"""\
<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.8.5" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Hi" Location="{rb.encode_location(src)}" Kind="FLAC File"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="P" Type="1" KeyType="0" Entries="1">
        <TRACK Key="1"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
""",
                encoding="utf-8",
            )

            def boom(*_a: object, **_k: object) -> tuple[str, bool, int, int]:
                raise rb.CancelledError("conversion cancelled")

            with patch.object(ffmpeg_tools, "require_tools", return_value=[]), patch.object(
                ffmpeg_tools,
                "run_ffprobe",
                return_value={
                    "streams": [
                        {
                            "codec_name": "flac",
                            "sample_fmt": "s32",
                            "bits_per_raw_sample": "24",
                            "sample_rate": "48000",
                            "channels": "2",
                        }
                    ]
                },
            ), patch.object(convert_plan, "classify_source", side_effect=boom):
                with self.assertRaises(rb.CancelledError):
                    rb.prepare(
                        xml_path,
                        "P",
                        root / "out",
                        root / "import.xml",
                    )


if __name__ == "__main__":
    unittest.main()
