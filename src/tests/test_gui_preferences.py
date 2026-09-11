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
            source_xml = Path(tmp) / "Rekordbox-collection.xml"
            source_xml.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            save_preferences(
                wav_dir,
                import_xml,
                source_xml=source_xml,
                output_format="aiff",
                bit_depth=24,
                sample_rate=48000,
                config_path=config,
            )
            loaded = load_preferences(config_path=config)
            self.assertEqual(loaded["wav_dir"], str(wav_dir.resolve()))
            self.assertEqual(loaded["import_xml"], str(import_xml.resolve()))
            self.assertEqual(loaded["source_xml"], str(source_xml.resolve()))
            self.assertEqual(loaded["output_format"], "aiff")
            self.assertEqual(loaded["bit_depth"], "24")
            self.assertEqual(loaded["sample_rate"], "48000")

    def test_load_preferences_ignores_invalid_output_format(self) -> None:
        from gui_preferences import load_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            config.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "wav_dir": "/tmp/x",
                        "import_xml": "/tmp/y.xml",
                        "output_format": "mp3",
                        "bit_depth": "32",
                        "sample_rate": "96000",
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_preferences(config_path=config)
            self.assertEqual(loaded["wav_dir"], "/tmp/x")
            self.assertNotIn("output_format", loaded)
            self.assertNotIn("bit_depth", loaded)
            self.assertNotIn("sample_rate", loaded)

    def test_save_without_source_xml_preserves_existing_source_xml(self) -> None:
        from gui_preferences import load_preferences, save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            wav_dir = Path(tmp) / "wav-out"
            wav_dir.mkdir()
            import_xml = wav_dir / "import.xml"
            source_xml = Path(tmp) / "Rekordbox-collection.xml"
            source_xml.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            save_preferences(
                wav_dir, import_xml, source_xml=source_xml, config_path=config
            )
            new_wav = Path(tmp) / "wav-out-2"
            new_wav.mkdir()
            new_import = new_wav / "import.xml"
            save_preferences(new_wav, new_import, config_path=config)
            loaded = load_preferences(config_path=config)
            self.assertEqual(loaded["wav_dir"], str(new_wav.resolve()))
            self.assertEqual(loaded["import_xml"], str(new_import.resolve()))
            self.assertEqual(Path(loaded["source_xml"]), source_xml.resolve())

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


class DefaultOutputPathsTests(unittest.TestCase):
    def test_default_output_paths_uses_documents_when_access_ok(self) -> None:
        from gui_preferences import default_output_paths

        wav_dir, import_xml = default_output_paths(documents_accessible=True)
        self.assertEqual(wav_dir, Path.home() / "Documents" / "rekordbox-converter")
        self.assertEqual(
            import_xml,
            Path.home() / "Documents" / "rekordbox-converter" / "rekordbox-import.xml",
        )

    def test_default_output_paths_uses_home_when_documents_not_accessible(self) -> None:
        from gui_preferences import default_output_paths

        wav_dir, import_xml = default_output_paths(documents_accessible=False)
        self.assertEqual(wav_dir, Path.home() / "rekordbox-converter")
        self.assertEqual(
            import_xml,
            Path.home() / "rekordbox-converter" / "rekordbox-import.xml",
        )


class ProbeFolderAccessTests(unittest.TestCase):
    def test_probe_folder_access_returns_false_when_probe_times_out(self) -> None:
        from gui_preferences import probe_folder_access
        import time

        def slow() -> bool:
            time.sleep(2.0)
            return True

        self.assertFalse(
            probe_folder_access(slow, timeout_seconds=0.05)
        )

    def test_probe_folder_access_returns_false_when_probe_raises(self) -> None:
        from gui_preferences import probe_folder_access

        def boom() -> bool:
            raise OSError("denied")

        self.assertFalse(
            probe_folder_access(boom, timeout_seconds=1.0)
        )

    def test_probe_path_via_child_returns_true_when_child_exits_zero(self) -> None:
        from gui_preferences import probe_path_via_child
        import subprocess

        def ok(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(args=["test"], returncode=0)

        self.assertTrue(probe_path_via_child(Path("/tmp"), run=ok))

    def test_probe_path_via_child_returns_false_when_child_exits_nonzero(self) -> None:
        from gui_preferences import probe_path_via_child
        import subprocess

        def denied(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(args=["test"], returncode=1)

        self.assertFalse(probe_path_via_child(Path("/tmp"), run=denied))

    def test_probe_path_via_child_returns_false_when_child_times_out(self) -> None:
        from gui_preferences import probe_path_via_child
        import subprocess

        def hang(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            raise subprocess.TimeoutExpired(cmd="test", timeout=0.05)

        self.assertFalse(
            probe_path_via_child(Path("/tmp"), timeout_seconds=0.05, run=hang)
        )

    def test_documents_probe_command_uses_this_app_not_system_test(self) -> None:
        from gui_preferences import documents_probe_command

        cmd = documents_probe_command(Path("/tmp/Documents"))
        self.assertIn("--probe-documents", cmd)
        self.assertEqual(cmd[-1], "/tmp/Documents")
        self.assertNotEqual(cmd[0], "/bin/test")

    def test_run_documents_probe_cli_exits_zero_for_existing_dir(self) -> None:
        from gui_preferences import run_documents_probe_cli

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_documents_probe_cli(["--probe-documents", tmp]), 0)

    def test_run_documents_probe_cli_exits_one_for_missing_path(self) -> None:
        from gui_preferences import run_documents_probe_cli

        self.assertEqual(
            run_documents_probe_cli(["--probe-documents", "/no/such/documents-dir"]),
            1,
        )


class IterRekordboxXmlFilesTests(unittest.TestCase):
    def test_iter_rekordbox_xml_files_finds_nested_documents_hit(self) -> None:
        from gui_preferences import iter_rekordbox_xml_files

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hit = (
                home
                / "Documents"
                / "rekordbox"
                / "Playlists"
                / "Rekordbox-collection.xml"
            )
            hit.parent.mkdir(parents=True)
            hit.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            (home / "Documents" / "playlist.xml").write_text("x", encoding="utf-8")
            found = iter_rekordbox_xml_files(home)
            self.assertEqual(found, [hit.resolve()])

    def test_iter_rekordbox_xml_files_finds_home_root_and_skips_siblings(self) -> None:
        from gui_preferences import iter_rekordbox_xml_files

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root_hit = home / "rekordbox.xml"
            root_hit.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            docs_hit = home / "Documents" / "Rekordbox-collection.xml"
            docs_hit.parent.mkdir(parents=True)
            docs_hit.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            for sibling in ("Desktop", "Downloads", "Music"):
                bad = home / sibling / "rekordbox.xml"
                bad.parent.mkdir(parents=True)
                bad.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            import_xml = home / "Documents" / "rekordbox-import.xml"
            import_xml.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            library_hit = home / "Library" / "Caches" / "rekordbox.xml"
            library_hit.parent.mkdir(parents=True)
            library_hit.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            found = iter_rekordbox_xml_files(home)
            self.assertEqual(
                found,
                sorted([docs_hit.resolve(), root_hit.resolve()], key=lambda p: str(p).casefold()),
            )

    def test_iter_rekordbox_xml_files_skips_icloud_under_documents(self) -> None:
        from gui_preferences import iter_rekordbox_xml_files

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            keep = home / "Documents" / "rekordbox.xml"
            keep.parent.mkdir(parents=True)
            keep.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            for name in ("iCloud Drive", "Mobile Documents", "Desktop", "Downloads"):
                bad = home / "Documents" / name / "rekordbox.xml"
                bad.parent.mkdir(parents=True)
                bad.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            found = iter_rekordbox_xml_files(home)
            self.assertEqual(found, [keep.resolve()])


class FindRekordboxXmlChildTests(unittest.TestCase):
    def test_find_rekordbox_xml_command_uses_this_app(self) -> None:
        from gui_preferences import FIND_REKORDBOX_XML_FLAG, find_rekordbox_xml_command

        cmd = find_rekordbox_xml_command(Path("/tmp/home"))
        self.assertIn(FIND_REKORDBOX_XML_FLAG, cmd)
        self.assertEqual(cmd[-1], "/tmp/home")
        self.assertNotEqual(cmd[0], "/bin/test")

    def test_run_find_rekordbox_xml_cli_prints_paths(self) -> None:
        from gui_preferences import run_find_rekordbox_xml_cli
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hit = home / "Documents" / "rekordbox.xml"
            hit.parent.mkdir(parents=True)
            hit.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_find_rekordbox_xml_cli(
                    ["--find-rekordbox-xml", str(home)]
                )
            self.assertEqual(code, 0)
            lines = [line for line in buf.getvalue().splitlines() if line.strip()]
            self.assertEqual(lines, [str(hit.resolve())])

    def test_find_rekordbox_xml_via_child_parses_stdout(self) -> None:
        from gui_preferences import find_rekordbox_xml_via_child
        import subprocess

        def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["probe"],
                returncode=0,
                stdout="/tmp/a/rekordbox.xml\n/tmp/b/Rekordbox-collection.xml\n",
                stderr="",
            )

        found = find_rekordbox_xml_via_child(Path("/tmp"), run=fake_run)
        self.assertEqual(
            found,
            [
                Path("/tmp/a/rekordbox.xml"),
                Path("/tmp/b/Rekordbox-collection.xml"),
            ],
        )

    def test_find_rekordbox_xml_via_child_returns_empty_on_error(self) -> None:
        from gui_preferences import find_rekordbox_xml_via_child
        import subprocess

        def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise OSError("fail")

        self.assertEqual(find_rekordbox_xml_via_child(Path("/tmp"), run=boom), [])


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

    def test_resolve_startup_paths_skips_documents_saved_when_not_accessible(self) -> None:
        from gui_preferences import default_output_paths, resolve_startup_paths

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            docs_wav = home / "Documents" / "rekordbox-converter"
            docs_wav.mkdir(parents=True)
            fallback_wav, fallback_xml = default_output_paths(
                documents_accessible=False, home=home
            )
            saved = {
                "wav_dir": str(docs_wav),
                "import_xml": str(docs_wav / "rekordbox-import.xml"),
            }
            wav, xml = resolve_startup_paths(
                saved,
                default_wav_dir=fallback_wav,
                default_import_xml=fallback_xml,
                documents_accessible=False,
                home=home,
            )
            self.assertEqual(wav, fallback_wav)
            self.assertEqual(xml, fallback_xml)

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
            self.assertEqual(xml, (wav_dir / "rekordbox-import.xml").resolve())

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
            self.assertEqual(xml, (wav_dir / "rekordbox-import.xml").resolve())


if __name__ == "__main__":
    unittest.main()
