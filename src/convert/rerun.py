"""Read-only change-aware rerun classification for manifest v2."""

from __future__ import annotations

from typing import Any

from convert.models import PlannedTrack


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
    return "reuse"
