#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rb_converter_gui import DEFAULT_OUTPUT, DEFAULT_WAV_DIR


class LoadPreferencesTests(unittest.TestCase):
    def test_load_preferences_returns_empty_when_file_missing(self) -> None:
        from gui_preferences import load_preferences

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "preferences.json"
            self.assertFalse(missing.exists())
            result = load_preferences(config_path=missing)
            self.assertEqual(result, {})

    def test_save_and_load_round_trip(self) -> None:
        from gui_preferences import load_preferences, save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            wav_dir = Path(tmp) / "wav-out"
            wav_dir.mkdir()
            import_xml = wav_dir / "import.xml"
            save_preferences(wav_dir, import_xml, config_path=config)
            loaded = load_preferences(config_path=config)
            self.assertEqual(loaded["wav_dir"], str(wav_dir.resolve()))
            self.assertEqual(loaded["import_xml"], str(import_xml.resolve()))

    def test_load_preferences_tolerates_corrupt_json(self) -> None:
        from gui_preferences import load_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            config.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(load_preferences(config_path=config), {})

    def test_load_preferences_ignores_wrong_version(self) -> None:
        from gui_preferences import load_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            config.write_text(
                json.dumps({"version": 99, "wav_dir": "/tmp/x", "import_xml": "/tmp/y.xml"}),
                encoding="utf-8",
            )
            self.assertEqual(load_preferences(config_path=config), {})


class ResolveStartupPathsTests(unittest.TestCase):
    def test_resolve_startup_paths_uses_defaults_when_saved_empty(self) -> None:
        from gui_preferences import resolve_startup_paths

        wav_dir, import_xml = resolve_startup_paths(
            {},
            default_wav_dir=DEFAULT_WAV_DIR,
            default_import_xml=DEFAULT_OUTPUT,
        )
        self.assertEqual(wav_dir, DEFAULT_WAV_DIR)
        self.assertEqual(import_xml, DEFAULT_OUTPUT)

    def test_resolve_startup_paths_restores_valid_saved_paths(self) -> None:
        from gui_preferences import resolve_startup_paths

        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "saved-wav"
            wav_dir.mkdir()
            import_xml = wav_dir / "my-import.xml"
            saved = {
                "wav_dir": str(wav_dir),
                "import_xml": str(import_xml),
            }
            wav, xml = resolve_startup_paths(
                saved,
                default_wav_dir=DEFAULT_WAV_DIR,
                default_import_xml=DEFAULT_OUTPUT,
            )
            self.assertEqual(wav, wav_dir.resolve())
            self.assertEqual(xml, import_xml.resolve())

    def test_resolve_startup_paths_falls_back_when_saved_wav_dir_missing(self) -> None:
        from gui_preferences import resolve_startup_paths

        saved = {"wav_dir": "/nonexistent/path/wav", "import_xml": "/tmp/x.xml"}
        wav_dir, import_xml = resolve_startup_paths(
            saved,
            default_wav_dir=DEFAULT_WAV_DIR,
            default_import_xml=DEFAULT_OUTPUT,
        )
        self.assertEqual(wav_dir, DEFAULT_WAV_DIR)
        self.assertEqual(import_xml, DEFAULT_OUTPUT)

    def test_resolve_startup_paths_partial_restore_wav_only(self) -> None:
        from gui_preferences import resolve_startup_paths

        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "only-wav"
            wav_dir.mkdir()
            saved = {"wav_dir": str(wav_dir)}
            wav, xml = resolve_startup_paths(
                saved,
                default_wav_dir=DEFAULT_WAV_DIR,
                default_import_xml=DEFAULT_OUTPUT,
            )
            self.assertEqual(wav, wav_dir.resolve())
            self.assertEqual(xml, (wav_dir / "rekordbox-wav-import.xml").resolve())

    def test_resolve_startup_paths_derives_import_xml_when_saved_xml_invalid(self) -> None:
        from gui_preferences import resolve_startup_paths

        with tempfile.TemporaryDirectory() as tmp:
            wav_dir = Path(tmp) / "saved-wav"
            wav_dir.mkdir()
            saved = {
                "wav_dir": str(wav_dir),
                "import_xml": "/nonexistent/parent/import.xml",
            }
            wav, xml = resolve_startup_paths(
                saved,
                default_wav_dir=DEFAULT_WAV_DIR,
                default_import_xml=DEFAULT_OUTPUT,
            )
            self.assertEqual(wav, wav_dir.resolve())
            self.assertEqual(xml, (wav_dir / "rekordbox-wav-import.xml").resolve())


if __name__ == "__main__":
    unittest.main()
