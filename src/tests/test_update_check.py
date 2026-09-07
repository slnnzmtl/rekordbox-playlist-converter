#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from update_check import (
    ReleaseInfo,
    UpdateCheckResult,
    check_for_update,
    fetch_latest_release,
    is_newer,
)


class IsNewerTests(unittest.TestCase):
    def test_is_newer_when_patch_bump_available(self) -> None:
        self.assertTrue(is_newer("1.0.0", "1.0.1"))

    def test_is_newer_false_when_versions_equal(self) -> None:
        self.assertFalse(is_newer("1.0.0", "1.0.0"))

    def test_is_newer_strips_v_prefix(self) -> None:
        self.assertTrue(is_newer("1.0.0", "v1.0.1"))

    def test_is_newer_treats_missing_patch_as_zero(self) -> None:
        self.assertFalse(is_newer("1.0", "1.0.0"))


class FetchLatestReleaseTests(unittest.TestCase):
    def test_fetch_latest_release_parses_stable_release(self) -> None:
        payload = {
            "tag_name": "v1.0.1",
            "html_url": "https://github.com/slnnzmtl/rekordbox-playlist-converter/releases/tag/v1.0.1",
            "draft": False,
            "prerelease": False,
            "body": "Bug fixes.",
        }
        response = BytesIO(json.dumps(payload).encode())

        with patch("update_check.urllib.request.urlopen", return_value=response):
            release = fetch_latest_release()

        self.assertEqual(
            release,
            ReleaseInfo(
                version="1.0.1",
                tag_name="v1.0.1",
                html_url="https://github.com/slnnzmtl/rekordbox-playlist-converter/releases/tag/v1.0.1",
                release_notes="Bug fixes.",
            ),
        )

    def test_fetch_latest_release_rejects_prerelease(self) -> None:
        payload = {
            "tag_name": "v2.0.0-beta",
            "html_url": "https://example.com/release",
            "draft": False,
            "prerelease": True,
            "body": "",
        }
        response = BytesIO(json.dumps(payload).encode())

        with patch("update_check.urllib.request.urlopen", return_value=response):
            with self.assertRaises(ValueError):
                fetch_latest_release()


class CheckForUpdateTests(unittest.TestCase):
    def test_check_for_update_returns_update_available(self) -> None:
        payload = {
            "tag_name": "v1.0.1",
            "html_url": "https://example.com/v1.0.1",
            "draft": False,
            "prerelease": False,
            "body": "Notes",
        }
        response = BytesIO(json.dumps(payload).encode())

        with patch("update_check.urllib.request.urlopen", return_value=response):
            result = check_for_update("1.0.0")

        self.assertTrue(result.is_update_available)
        self.assertEqual(result.release.version, "1.0.1")

    def test_check_for_update_returns_up_to_date(self) -> None:
        payload = {
            "tag_name": "v1.0.0",
            "html_url": "https://example.com/v1.0.0",
            "draft": False,
            "prerelease": False,
            "body": "",
        }
        response = BytesIO(json.dumps(payload).encode())

        with patch("update_check.urllib.request.urlopen", return_value=response):
            result = check_for_update("1.0.0")

        self.assertTrue(result.is_up_to_date)

    def test_check_for_update_returns_error_on_network_failure(self) -> None:
        with patch(
            "update_check.urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            result = check_for_update("1.0.0")

        self.assertTrue(result.is_error)
        self.assertIn("timed out", result.message)


if __name__ == "__main__":
    unittest.main()
