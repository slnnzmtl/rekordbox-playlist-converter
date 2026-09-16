#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from analytics import (
    INGEST_URL,
    POST_OK,
    POST_REJECT,
    POST_RETRY,
    POST_TIMEOUT_SECONDS,
    build_conversion_payload,
    build_failure_payload,
    build_install_payload,
    enqueue,
    post_event,
)
from convert.models import ConvertStats
from version import __version__


class BuildInstallPayloadTests(unittest.TestCase):
    def test_build_install_payload_has_exact_v1_fields(self) -> None:
        """Given surface and install_id: When build_install_payload runs:
        Then the dict has only the slim install contract fields."""
        payload = build_install_payload(
            surface="gui",
            install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        self.assertEqual(
            payload,
            {
                "schema_version": 1,
                "event": "install",
                "app_version": __version__,
                "surface": "gui",
                "install_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            },
        )


class BuildFailurePayloadTests(unittest.TestCase):
    def test_build_failure_payload_has_exact_v1_fields(self) -> None:
        """Given surface, install_id, and reason: When build_failure_payload runs:
        Then the dict has only the slim conversion_failed contract fields."""
        payload = build_failure_payload(
            surface="gui",
            install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            reason="xml_parse",
        )
        self.assertEqual(
            payload,
            {
                "schema_version": 1,
                "event": "conversion_failed",
                "app_version": __version__,
                "surface": "gui",
                "install_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "reason": "xml_parse",
            },
        )
        for key in (
            "rekordbox_version",
            "output_format",
            "bit_depth",
            "sample_rate",
            "outcomes",
            "input_file_types",
        ):
            self.assertNotIn(key, payload)

    def test_build_failure_payload_unknown_reason_clamps_to_unknown(self) -> None:
        """Given an invalid reason: When build_failure_payload runs: Then reason
        is unknown."""
        payload = build_failure_payload(
            surface="cli",
            install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            reason="not-a-reason",
        )
        self.assertEqual(payload["reason"], "unknown")
        self.assertEqual(payload["event"], "conversion_failed")


class BuildConversionPayloadTests(unittest.TestCase):
    def test_build_conversion_payload_has_exact_v1_fields(self) -> None:
        """Given stats, PRODUCT Version, and empty source_paths: When
        build_conversion_payload runs: Then the dict has conversion_completed
        fields including zeroed input_file_types."""
        root = ET.Element("DJ_PLAYLISTS")
        ET.SubElement(
            root, "PRODUCT", {"Name": "rekordbox", "Version": "7.0.5", "Company": "X"}
        )
        stats = ConvertStats(converted=12, copied=3, skipped=1, appended=15)
        payload = build_conversion_payload(
            surface="cli",
            install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            source_root=root,
            output_format="wav",
            bit_depth=24,
            sample_rate=48000,
            stats=stats,
            source_paths=(),
        )
        self.assertEqual(
            payload,
            {
                "schema_version": 1,
                "event": "conversion_completed",
                "app_version": __version__,
                "rekordbox_version": "7.0.5",
                "surface": "cli",
                "output_format": "wav",
                "bit_depth": "24",
                "sample_rate": "48000",
                "outcomes": {
                    "converted": 12,
                    "copied": 3,
                    "skipped": 1,
                    "appended": 15,
                },
                "input_file_types": {
                    "mp3": 0,
                    "wav": 0,
                    "aiff": 0,
                    "flac": 0,
                    "m4a": 0,
                    "alac": 0,
                    "other": 0,
                },
                "install_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            },
        )

    def test_build_conversion_payload_buckets_source_extensions(self) -> None:
        """Given mixed source paths including aliases: When build_conversion_payload
        runs: Then input_file_types counts known buckets and other."""
        stats = ConvertStats(converted=1)
        payload = build_conversion_payload(
            surface="gui",
            install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            source_root=None,
            output_format="wav",
            bit_depth=24,
            sample_rate=48000,
            stats=stats,
            source_paths=[
                Path("/x/a.flac"),
                Path("/x/b.WAV"),
                Path("/x/c.wave"),
                Path("/x/d.aif"),
                Path("/x/e.aiff"),
                Path("/x/f.mp3"),
                Path("/x/g.m4a"),
                Path("/x/h.alac"),
                Path("/x/i.ogg"),
                "/x/j.caf",
            ],
        )
        self.assertEqual(
            payload["input_file_types"],
            {
                "mp3": 1,
                "wav": 2,
                "aiff": 2,
                "flac": 1,
                "m4a": 1,
                "alac": 1,
                "other": 2,
            },
        )

    def test_build_conversion_payload_clamps_input_file_type_counts(self) -> None:
        """Given more than 10000 sources of one type: When build runs: Then
        that bucket is clamped to 10000."""
        stats = ConvertStats(converted=1)
        paths = [Path(f"/x/{i}.flac") for i in range(10001)]
        payload = build_conversion_payload(
            surface="cli",
            install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            source_root=None,
            output_format="wav",
            bit_depth=24,
            sample_rate=48000,
            stats=stats,
            source_paths=paths,
        )
        self.assertEqual(payload["input_file_types"]["flac"], 10000)


class PostEventTests(unittest.TestCase):
    def test_post_event_posts_json_to_ingest_url(self) -> None:
        """Given a payload: When post_event runs: Then it POSTs JSON to the
        hardcoded ingest URL with a short timeout and returns POST_OK."""
        payload = build_install_payload(
            surface="gui",
            install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        response = BytesIO(b'{"status":"accepted"}')
        with patch("analytics.urllib.request.urlopen", return_value=response) as urlopen:
            result = post_event(payload)
        self.assertEqual(result, POST_OK)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, INGEST_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            payload,
        )
        self.assertEqual(urlopen.call_args.kwargs.get("timeout"), POST_TIMEOUT_SECONDS)

    def test_post_event_swallows_network_errors(self) -> None:
        """Given urlopen raises: When post_event runs: Then it does not raise
        and returns POST_RETRY."""
        with patch(
            "analytics.urllib.request.urlopen",
            side_effect=OSError("offline"),
        ):
            result = post_event({"schema_version": 1, "event": "install"})
        self.assertEqual(result, POST_RETRY)

    def test_post_event_http_400_is_reject(self) -> None:
        """Given urlopen raises HTTPError 400: When post_event runs: Then
        it returns POST_REJECT so the queue can drop a poison head."""
        import urllib.error

        err = urllib.error.HTTPError(
            INGEST_URL, 400, "Bad Request", hdrs=None, fp=BytesIO(b'{"status":"invalid"}')
        )
        with patch("analytics.urllib.request.urlopen", side_effect=err):
            result = post_event({"schema_version": 1, "event": "install"})
        self.assertEqual(result, POST_REJECT)


def _run_thread_inline(*_args, **kwargs):
    """Return a Thread mock that runs target(*args) on start()."""
    target = kwargs.get("target") or (_args[0] if _args else None)
    args = kwargs.get("args", ())
    thread = MagicMock()

    def start() -> None:
        if target is not None:
            target(*args)

    thread.start = start
    return thread


class EnqueueCacheTests(unittest.TestCase):
    def test_failed_post_leaves_event_in_queue_file(self) -> None:
        """Given analytics on and urlopen fails: When enqueue runs: Then the
        payload remains in analytics_queue.json beside preferences."""
        import analytics
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            payload = build_install_payload(
                surface="gui",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
            with patch(
                "analytics.urllib.request.urlopen",
                side_effect=OSError("offline"),
            ), patch("analytics.threading.Thread", side_effect=_run_thread_inline):
                enqueue(payload, config_path=config)

            queue_path = config.parent / "analytics_queue.json"
            self.assertTrue(queue_path.is_file())
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["events"], [payload])

    def test_successful_post_removes_event_from_queue_file(self) -> None:
        """Given analytics on and urlopen succeeds: When enqueue runs: Then
        analytics_queue.json is empty."""
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            payload = build_install_payload(
                surface="gui",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
            response = BytesIO(b'{"status":"accepted"}')
            with patch(
                "analytics.urllib.request.urlopen",
                return_value=response,
            ), patch("analytics.threading.Thread", side_effect=_run_thread_inline):
                enqueue(payload, config_path=config)

            queue_path = config.parent / "analytics_queue.json"
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["events"], [])

    def test_flush_stops_on_first_failure_preserving_order(self) -> None:
        """Given two queued events and the first POST fails: When flush runs:
        Then both events remain in order."""
        import analytics
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            first = build_install_payload(
                surface="gui",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
            second = build_conversion_payload(
                surface="gui",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                source_root=None,
                output_format="wav",
                bit_depth=24,
                sample_rate=48000,
                stats=ConvertStats(converted=1),
                source_paths=(),
            )
            queue_path = config.parent / "analytics_queue.json"
            queue_path.write_text(
                json.dumps({"version": 1, "events": [first, second]}, indent=2)
                + "\n",
                encoding="utf-8",
            )
            with patch(
                "analytics.urllib.request.urlopen",
                side_effect=OSError("offline"),
            ), patch("analytics.threading.Thread", side_effect=_run_thread_inline):
                analytics.flush_pending(config_path=config)

            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["events"], [first, second])

    def test_flush_drops_http_400_head_and_continues(self) -> None:
        """Given a poison 400 head then a good event: When flush runs: Then the
        head is dropped and the second event is sent."""
        import analytics
        import urllib.error
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            first = build_install_payload(
                surface="gui",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
            second = build_conversion_payload(
                surface="gui",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                source_root=None,
                output_format="wav",
                bit_depth=24,
                sample_rate=48000,
                stats=ConvertStats(converted=1),
                source_paths=(),
            )
            queue_path = config.parent / "analytics_queue.json"
            queue_path.write_text(
                json.dumps({"version": 1, "events": [first, second]}, indent=2)
                + "\n",
                encoding="utf-8",
            )
            posted: list[dict] = []

            def side_effect(request, timeout=None):
                payload = json.loads(request.data.decode("utf-8"))
                posted.append(payload)
                if payload["event"] == "install":
                    raise urllib.error.HTTPError(
                        INGEST_URL,
                        400,
                        "Bad Request",
                        hdrs=None,
                        fp=BytesIO(b'{"status":"invalid"}'),
                    )
                return BytesIO(b'{"status":"accepted"}')

            with patch(
                "analytics.urllib.request.urlopen",
                side_effect=side_effect,
            ), patch("analytics.threading.Thread", side_effect=_run_thread_inline):
                analytics.flush_pending(config_path=config)

            self.assertEqual([p["event"] for p in posted], ["install", "conversion_completed"])
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["events"], [])

    def test_flush_drains_queue_in_order_on_success(self) -> None:
        """Given two queued events and successful POSTs: When flush runs: Then
        both are sent in order and the queue is empty."""
        import analytics
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            first = build_install_payload(
                surface="cli",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
            second = build_conversion_payload(
                surface="cli",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                source_root=None,
                output_format="aiff",
                bit_depth=16,
                sample_rate=44100,
                stats=ConvertStats(converted=2),
                source_paths=(),
            )
            queue_path = config.parent / "analytics_queue.json"
            queue_path.write_text(
                json.dumps({"version": 1, "events": [first, second]}, indent=2)
                + "\n",
                encoding="utf-8",
            )
            posted: list[dict] = []

            def capture(request, timeout=None):
                posted.append(json.loads(request.data.decode("utf-8")))
                return BytesIO(b'{"status":"accepted"}')

            with patch(
                "analytics.urllib.request.urlopen",
                side_effect=capture,
            ), patch("analytics.threading.Thread", side_effect=_run_thread_inline):
                analytics.flush_pending(config_path=config)

            self.assertEqual(posted, [first, second])
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["events"], [])

    def test_flush_pending_noops_when_analytics_off(self) -> None:
        """Given analytics off and a queued event: When flush_pending runs:
        Then urlopen is not called and the event remains."""
        import analytics
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="off",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            payload = build_install_payload(
                surface="gui",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
            queue_path = config.parent / "analytics_queue.json"
            queue_path.write_text(
                json.dumps({"version": 1, "events": [payload]}, indent=2) + "\n",
                encoding="utf-8",
            )
            with patch(
                "analytics.urllib.request.urlopen"
            ) as urlopen, patch(
                "analytics.threading.Thread", side_effect=_run_thread_inline
            ):
                analytics.flush_pending(config_path=config)
            urlopen.assert_not_called()
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["events"], [payload])

    def test_reopt_in_flushes_held_queue_without_new_install(self) -> None:
        """Given existing install_id and a held queue: When enable_analytics
        runs: Then the queued event is POSTed and no second install is minted."""
        import analytics
        from gui_prefs import load_preferences, save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            install_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            save_preferences(
                analytics="off",
                install_id=install_id,
                config_path=config,
            )
            held = build_conversion_payload(
                surface="gui",
                install_id=install_id,
                source_root=None,
                output_format="wav",
                bit_depth=24,
                sample_rate=48000,
                stats=ConvertStats(converted=1),
                source_paths=(),
            )
            queue_path = config.parent / "analytics_queue.json"
            queue_path.write_text(
                json.dumps({"version": 1, "events": [held]}, indent=2) + "\n",
                encoding="utf-8",
            )
            posted: list[dict] = []

            def capture(request, timeout=None):
                posted.append(json.loads(request.data.decode("utf-8")))
                return BytesIO(b'{"status":"accepted"}')

            with patch.object(
                analytics, "default_config_path", return_value=config
            ), patch(
                "analytics.urllib.request.urlopen",
                side_effect=capture,
            ), patch("analytics.threading.Thread", side_effect=_run_thread_inline):
                returned = analytics.enable_analytics(surface="gui")

            self.assertEqual(returned, install_id)
            self.assertEqual(posted, [held])
            loaded = load_preferences(config_path=config)
            self.assertEqual(loaded["analytics"], "on")
            self.assertEqual(loaded["install_id"], install_id)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["events"], [])


class EnableAnalyticsTests(unittest.TestCase):
    def test_enable_analytics_mints_install_id_and_enqueues_install_once(self) -> None:
        """Given no install_id: When enable_analytics runs twice: Then one
        install event is enqueued and install_id is persisted."""
        import analytics
        from gui_prefs import load_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            posted: list[dict] = []

            def capture(payload: dict, *, config_path=None) -> None:
                posted.append(payload)

            with patch.object(analytics, "default_config_path", return_value=config), patch.object(
                analytics, "enqueue", side_effect=capture
            ):
                first = analytics.enable_analytics(surface="gui")
                second = analytics.enable_analytics(surface="gui")

            self.assertEqual(len(posted), 1)
            self.assertEqual(posted[0]["event"], "install")
            self.assertEqual(posted[0]["surface"], "gui")
            self.assertEqual(posted[0]["install_id"], first)
            self.assertEqual(first, second)
            loaded = load_preferences(config_path=config)
            self.assertEqual(loaded["analytics"], "on")
            self.assertEqual(loaded["install_id"], first)

    def test_disable_analytics_keeps_install_id(self) -> None:
        """Given analytics on with install_id: When disable_analytics runs:
        Then analytics is off and install_id remains."""
        import analytics
        from gui_prefs import load_preferences, save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            with patch.object(analytics, "default_config_path", return_value=config):
                analytics.disable_analytics()
            loaded = load_preferences(config_path=config)
            self.assertEqual(loaded["analytics"], "off")
            self.assertEqual(
                loaded["install_id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            )

    def test_report_conversion_noops_when_consent_off(self) -> None:
        """Given analytics off: When report_conversion runs: Then nothing is
        enqueued."""
        import analytics
        from convert.models import ConvertStats

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            with patch.object(analytics, "default_config_path", return_value=config), patch.object(
                analytics, "enqueue"
            ) as enqueue:
                analytics.report_conversion(
                    surface="cli",
                    source_root=None,
                    output_format="wav",
                    bit_depth=24,
                    sample_rate=48000,
                    stats=ConvertStats(converted=1),
                    source_paths=(),
                )
            enqueue.assert_not_called()

    def test_report_conversion_enqueues_when_consent_on(self) -> None:
        """Given analytics on with install_id: When report_conversion runs:
        Then conversion_completed is enqueued."""
        import analytics
        from convert.models import ConvertStats
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            posted: list[dict] = []

            def capture(payload: dict, *, config_path=None) -> None:
                posted.append(payload)

            with patch.object(analytics, "default_config_path", return_value=config), patch.object(
                analytics, "enqueue", side_effect=capture
            ):
                analytics.report_conversion(
                    surface="cli",
                    source_root=None,
                    output_format="aiff",
                    bit_depth=16,
                    sample_rate=44100,
                    stats=ConvertStats(converted=2, copied=1, skipped=0, appended=3),
                    source_paths=[Path("/x/a.flac"), Path("/x/b.mp3")],
                )
            self.assertEqual(len(posted), 1)
            self.assertEqual(posted[0]["event"], "conversion_completed")
            self.assertEqual(posted[0]["install_id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            self.assertEqual(posted[0]["surface"], "cli")
            self.assertEqual(
                posted[0]["input_file_types"],
                {
                    "mp3": 1,
                    "wav": 0,
                    "aiff": 0,
                    "flac": 1,
                    "m4a": 0,
                    "alac": 0,
                    "other": 0,
                },
            )

    def test_report_failure_noops_when_consent_off(self) -> None:
        """Given analytics off: When report_failure runs: Then nothing is
        enqueued."""
        import analytics

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            with patch.object(analytics, "default_config_path", return_value=config), patch.object(
                analytics, "_append_event"
            ) as append, patch.object(analytics, "_flush_sync") as flush:
                analytics.report_failure(surface="cli", reason="encode")
            append.assert_not_called()
            flush.assert_not_called()

    def test_report_failure_enqueues_when_consent_on(self) -> None:
        """Given analytics on with install_id: When report_failure runs:
        Then conversion_failed is appended and flushed synchronously."""
        import analytics
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            posted: list[dict] = []

            def capture(payload: dict, config_path=None) -> None:
                posted.append(payload)

            with patch.object(analytics, "default_config_path", return_value=config), patch.object(
                analytics, "_append_event", side_effect=capture
            ) as append, patch.object(analytics, "_flush_sync") as flush:
                analytics.report_failure(surface="gui", reason="config")
            append.assert_called_once()
            flush.assert_called_once()
            self.assertEqual(len(posted), 1)
            self.assertEqual(posted[0]["event"], "conversion_failed")
            self.assertEqual(posted[0]["reason"], "config")
            self.assertEqual(posted[0]["install_id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            self.assertEqual(posted[0]["surface"], "gui")

    def test_report_failure_sync_flush_posts_before_return(self) -> None:
        """Given analytics on: When report_failure runs with a working POST:
        Then the queue is empty after return (sync flush completed)."""
        import analytics
        from gui_prefs import save_preferences
        from io import BytesIO

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            with patch.object(analytics, "default_config_path", return_value=config), patch(
                "analytics.urllib.request.urlopen",
                return_value=BytesIO(b'{"status":"accepted"}'),
            ):
                analytics.report_failure(surface="cli", reason="xml_parse")
            queue_path = config.parent / "analytics_queue.json"
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["events"], [])

    def test_queue_accepts_conversion_failed_event(self) -> None:
        """Given a conversion_failed payload: When enqueue fails to POST: Then
        the event remains in the durable queue."""
        from gui_prefs import save_preferences

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "preferences.json"
            save_preferences(
                analytics="on",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                config_path=config,
            )
            payload = build_failure_payload(
                surface="cli",
                install_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                reason="encode",
            )
            with patch(
                "analytics.urllib.request.urlopen",
                side_effect=OSError("offline"),
            ), patch("analytics.threading.Thread", side_effect=_run_thread_inline):
                enqueue(payload, config_path=config)

            queue_path = config.parent / "analytics_queue.json"
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(data["events"], [payload])


if __name__ == "__main__":
    unittest.main()
