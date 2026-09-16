"""Opt-in privacy-preserving usage analytics client."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from convert.models import ConvertStats
from gui_prefs.storage import default_config_path, load_preferences, save_preferences
from version import __version__

INGEST_URL = "https://analytics.slnnzmtl.xyz/v1/events"
POST_TIMEOUT_SECONDS = 2.0
_OUTCOME_MAX = 10000
_USER_AGENT = "rekordbox-playlist-converter"
_QUEUE_VERSION = 1
_QUEUE_FILENAME = "analytics_queue.json"
_BASE_EVENT_KEYS = frozenset(
    {"schema_version", "event", "app_version", "surface", "install_id"}
)
_KNOWN_EVENTS = frozenset({"install", "conversion_completed"})

_queue_lock = threading.Lock()
_flush_in_flight = False

POST_OK = "ok"
POST_RETRY = "retry"
POST_REJECT = "reject"

_INPUT_FILE_TYPE_KEYS = ("mp3", "wav", "aiff", "flac", "m4a", "alac", "other")
_INPUT_EXT_ALIASES = {"aif": "aiff", "wave": "wav"}


def _clamp_outcome(value: int) -> int:
    if value < 0:
        return 0
    if value > _OUTCOME_MAX:
        return _OUTCOME_MAX
    return int(value)


def _rekordbox_version(source_root: ET.Element | None) -> str:
    if source_root is None:
        return ""
    product = source_root.find("PRODUCT")
    if product is None:
        return ""
    return product.get("Version") or ""


def _count_input_file_types(source_paths) -> dict[str, int]:
    counts = {key: 0 for key in _INPUT_FILE_TYPE_KEYS}
    for raw in source_paths:
        ext = Path(raw).suffix.lower().lstrip(".")
        ext = _INPUT_EXT_ALIASES.get(ext, ext)
        if ext in counts and ext != "other":
            counts[ext] += 1
        else:
            counts["other"] += 1
    return {key: _clamp_outcome(counts[key]) for key in _INPUT_FILE_TYPE_KEYS}


def _base_payload(*, surface: str, install_id: str, event: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event": event,
        "app_version": __version__,
        "surface": surface,
        "install_id": install_id,
    }


def build_install_payload(*, surface: str, install_id: str) -> dict[str, Any]:
    return _base_payload(surface=surface, install_id=install_id, event="install")


def build_conversion_payload(
    *,
    surface: str,
    install_id: str,
    source_root: ET.Element | None,
    output_format: str,
    bit_depth: int | str,
    sample_rate: int | str,
    stats: ConvertStats,
    source_paths,
) -> dict[str, Any]:
    payload = _base_payload(
        surface=surface, install_id=install_id, event="conversion_completed"
    )
    payload["rekordbox_version"] = _rekordbox_version(source_root)
    payload["output_format"] = str(output_format)
    payload["bit_depth"] = str(bit_depth)
    payload["sample_rate"] = str(sample_rate)
    payload["outcomes"] = {
        "converted": _clamp_outcome(stats.converted),
        "copied": _clamp_outcome(stats.copied),
        "skipped": _clamp_outcome(stats.skipped),
        "appended": _clamp_outcome(stats.appended),
    }
    payload["input_file_types"] = _count_input_file_types(source_paths)
    return payload


def post_event(payload: dict[str, Any]) -> str:
    """POST JSON to ingest.

    Returns POST_OK on success, POST_RETRY for transient failures (keep queued),
    POST_REJECT for permanent client errors (drop head so FIFO can advance).
    """
    try:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            INGEST_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=POST_TIMEOUT_SECONDS):
            pass
        return POST_OK
    except (OSError, urllib.error.URLError, ValueError, TypeError) as exc:
        if isinstance(exc, urllib.error.HTTPError) and 400 <= int(exc.code) < 500:
            return POST_REJECT
        return POST_RETRY


def _prefs_path(config_path=None) -> Path:
    return Path(config_path) if config_path is not None else default_config_path()


def _cache_path(config_path=None) -> Path:
    return _prefs_path(config_path).parent / _QUEUE_FILENAME


def _event_ok(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("event") not in _KNOWN_EVENTS:
        return False
    for key in _BASE_EVENT_KEYS:
        if key not in entry:
            return False
    return True


def _load_queue(config_path=None) -> list[dict[str, Any]]:
    path = _cache_path(config_path)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(raw, dict) or raw.get("version") != _QUEUE_VERSION:
        return []
    events = raw.get("events")
    if not isinstance(events, list):
        return []
    return [e for e in events if _event_ok(e)]


def _write_queue(events: list[dict[str, Any]], config_path=None) -> None:
    path = _cache_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": _QUEUE_VERSION, "events": events}
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=".analytics-queue-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _append_event(payload: dict[str, Any], config_path=None) -> None:
    events = _load_queue(config_path)
    events.append(payload)
    _write_queue(events, config_path)


def _peek_head(config_path=None) -> dict[str, Any] | None:
    events = _load_queue(config_path)
    if not events:
        return None
    return events[0]


def _pop_head_if_matches(head: dict[str, Any], config_path=None) -> None:
    events = _load_queue(config_path)
    if not events:
        return
    if events[0] != head:
        return
    _write_queue(events[1:], config_path)


def _flush_queue(config_path=None) -> None:
    global _flush_in_flight
    path = _prefs_path(config_path)
    failed = False
    try:
        while True:
            with _queue_lock:
                if not analytics_enabled(config_path=path):
                    return
                head = _peek_head(path)
                if head is None:
                    return
            result = post_event(head)
            if result == POST_RETRY:
                failed = True
                return
            with _queue_lock:
                _pop_head_if_matches(head, path)
    finally:
        with _queue_lock:
            _flush_in_flight = False
            need_again = (
                not failed
                and analytics_enabled(config_path=path)
                and _peek_head(path) is not None
            )
        if need_again:
            _start_flush(path)


def _start_flush(config_path=None) -> None:
    global _flush_in_flight
    path = _prefs_path(config_path)
    with _queue_lock:
        if _flush_in_flight:
            return
        if not analytics_enabled(config_path=path):
            return
        if _peek_head(path) is None:
            return
        _flush_in_flight = True
    threading.Thread(target=_flush_queue, args=(path,), daemon=True).start()


def enqueue(payload: dict[str, Any], *, config_path=None) -> None:
    """Append payload to the durable queue and flush when consent is on."""
    path = _prefs_path(config_path)
    try:
        with _queue_lock:
            _append_event(payload, path)
    except OSError:
        return
    _start_flush(path)


def flush_pending(*, config_path=None) -> None:
    """Retry queued events when analytics consent is on (fire-and-forget)."""
    _start_flush(config_path)


def analytics_enabled(*, config_path=None) -> bool:
    prefs = load_preferences(config_path=config_path or default_config_path())
    return prefs.get("analytics") == "on"


def enable_analytics(*, surface: str, config_path=None) -> str:
    """Persist consent on. Mint install_id and enqueue install once."""
    path = config_path or default_config_path()
    prefs = load_preferences(config_path=path)
    install_id = prefs.get("install_id")
    minted = False
    if not install_id:
        install_id = str(uuid.uuid4())
        minted = True
    save_preferences(
        analytics="on",
        install_id=install_id,
        config_path=path,
    )
    if minted:
        enqueue(
            build_install_payload(surface=surface, install_id=install_id),
            config_path=path,
        )
    flush_pending(config_path=path)
    return install_id


def disable_analytics(*, config_path=None) -> None:
    path = config_path or default_config_path()
    save_preferences(analytics="off", config_path=path)


def report_conversion(
    *,
    surface: str,
    source_root: ET.Element | None,
    output_format: str,
    bit_depth: int | str,
    sample_rate: int | str,
    stats: ConvertStats,
    source_paths,
    config_path=None,
) -> None:
    path = config_path or default_config_path()
    prefs = load_preferences(config_path=path)
    if prefs.get("analytics") != "on":
        return
    install_id = prefs.get("install_id")
    if not install_id:
        return
    enqueue(
        build_conversion_payload(
            surface=surface,
            install_id=install_id,
            source_root=source_root,
            output_format=output_format,
            bit_depth=bit_depth,
            sample_rate=sample_rate,
            stats=stats,
            source_paths=source_paths,
        ),
        config_path=path,
    )
