"""Read-only change-aware rerun classification for manifest v2."""

from __future__ import annotations

from typing import Any

from convert.freshness import assignment_state, metadata_signature, recipe_from_item
from convert.models import PlannedTrack
from convert.quality import coerce_output_format

_PCM_RECIPE_KEYS = ("format", "bit_depth", "sample_rate", "channels")


def _rebuild_action(item: PlannedTrack) -> str:
    return "rewrite_container" if item.passthrough else "transcode"


def _stats_match(stored: dict[str, Any] | None, current: dict[str, Any] | None) -> bool:
    if stored is None or current is None:
        return False
    return stored.get("size") == current.get("size") and stored.get(
        "mtime_ns"
    ) == current.get("mtime_ns")


def classify_assignment(
    *,
    item: PlannedTrack,
    record: dict[str, Any] | None,
    force: bool,
    dest_exists: bool,
    dest_stat: dict[str, Any] | None,
    source_stat: dict[str, Any] | None,
) -> str:
    if item.noop:
        return "in_place_noop"
    if not dest_exists:
        return "recreate_missing"
    record = record or {}
    state = assignment_state(record)
    if state == "complete" and not _stats_match(record.get("output"), dest_stat):
        return "external_modification_conflict"
    if state in {"incomplete", "unverified"}:
        return _rebuild_action(item)
    if force:
        return _rebuild_action(item)
    if not _stats_match(record.get("source"), source_stat):
        return _rebuild_action(item)
    stored_recipe = record.get("recipe") or {}
    current_recipe = recipe_from_item(item)
    if any(stored_recipe.get(key) != current_recipe.get(key) for key in _PCM_RECIPE_KEYS):
        return "transcode"
    if stored_recipe.get("revision") != current_recipe.get("revision"):
        return "rewrite_container"
    stored_meta = (record.get("metadata") or {}).get("signature")
    if stored_meta != metadata_signature(item.source_el):
        if coerce_output_format(item.output_format) == "aiff":
            return "update_metadata"
        return "refresh_xml"
    return "reuse"
