"""Convert plan dataclasses."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from convert.rerun import Decision
    from converter_manifest import ConverterManifest


@dataclass
class PlannedTrack:
    source_el: ET.Element
    source_path: Path
    dest_path: Path
    dest_location: str
    dest_name: str
    codec: str | None  # None means passthrough (or no-op)
    passthrough: bool
    noop: bool
    bit_depth: int = 24
    sample_rate: int = 48000
    duration_seconds: float | None = None
    output_format: str = "wav"


@dataclass
class ConversionPreviewItem:
    relative_dest: str
    action: str
    bit_depth: int
    sample_rate: int
    size_bytes: int | None = None
    size_display: str = "—"
    source_display: str = ""
    reason: str = ""
    write_kind: str = "none"
    reason_code: str = ""
    source_stat: dict | None = None
    dest_stat: dict | None = None


@dataclass
class ConversionPreview:
    selected: int
    resolved: int
    unique_outputs: int
    duplicates: int
    missing: int
    items: list[ConversionPreviewItem] = field(default_factory=list)
    decisions: dict[tuple[str, str], Decision] = field(default_factory=dict)


@dataclass
class ItemResult:
    source: Path
    destination: Path
    action: str
    outcome: str  # succeeded, conflict, state_changed, failed, cancelled
    playlists: tuple[str, ...] = ()
    error: str | None = None
    write: str = ""  # transcode, copy, or empty for non-audio outcomes


@dataclass
class Plan:
    playlist_name: str
    wav_playlist_name: str
    library_dir: Path
    media_dir: Path
    output: Path
    tracks: list[PlannedTrack]  # playlist order, may repeat dest
    unique: list[PlannedTrack]  # one per dest path
    source_root: ET.Element
    output_root: ET.Element
    output_existed: bool
    warnings: list[str] = field(default_factory=list)
    output_format: str = "wav"
    max_bit_depth: int = 24
    max_sample_rate: int = 48000
    cover_cache: dict[Path, bytes | None] = field(default_factory=dict)
    manifest: ConverterManifest | None = None


@dataclass
class ConvertStats:
    converted: int = 0
    copied: int = 0
    skipped: int = 0
    pcm_rebuilt: int = 0
    metadata_refreshed: int = 0
    reused: int = 0
    recreated: int = 0
    appended: int = 0
    errors: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    state_changed: list[str] = field(default_factory=list)
    item_results: list[ItemResult] = field(default_factory=list)
    # (source_key, format) that skipped, copied, or converted successfully.
    succeeded: set[tuple[str, str]] = field(default_factory=set)
    # Entries appended per plan by apply_xml during execute_prepared.
    appended_by_plan: list[int] = field(default_factory=list)


def _item_ref(result: ItemResult) -> str:
    return f"{result.source.name} → {result.destination.name}"


def apply_item_result_aggregates(stats: ConvertStats) -> ConvertStats:
    """Fill ConvertStats counters from item_results. Keep succeeded/appended."""
    results = stats.item_results
    if not results:
        return stats
    converted = copied = pcm = meta = reused = skipped = 0
    rec_tx = rec_copy = 0
    errors: list[str] = []
    conflicts: list[str] = []
    state_changed: list[str] = []
    for result in results:
        if result.outcome == "failed":
            errors.append(result.error or _item_ref(result))
            continue
        if result.outcome == "conflict":
            conflicts.append(_item_ref(result))
            continue
        if result.outcome == "state_changed":
            state_changed.append(_item_ref(result))
            continue
        if result.outcome != "succeeded":
            continue
        if result.action == "recreate_missing":
            if result.write == "copy":
                rec_copy += 1
            else:
                rec_tx += 1
        elif result.action == "rewrite_container":
            pcm += 1
        elif result.action in {"refresh_xml", "update_metadata"}:
            meta += 1
            skipped += 1
        elif result.action in {"reuse", "in_place_noop"}:
            reused += 1
            skipped += 1
        elif result.action == "copy" or result.write == "copy":
            copied += 1
        else:
            converted += 1
    stats.converted = converted
    stats.copied = copied
    stats.pcm_rebuilt = pcm
    stats.metadata_refreshed = meta
    stats.reused = reused
    stats.recreated = rec_tx + rec_copy
    stats.skipped = skipped
    stats.errors = errors
    stats.conflicts = conflicts
    stats.state_changed = state_changed
    return stats


def stats_for_playlist(
    stats: ConvertStats,
    playlist_name: str,
    *,
    appended: int = 0,
) -> ConvertStats:
    if not stats.item_results:
        return ConvertStats(
            converted=stats.converted,
            copied=stats.copied,
            skipped=stats.skipped,
            pcm_rebuilt=stats.pcm_rebuilt,
            metadata_refreshed=stats.metadata_refreshed,
            reused=stats.reused,
            recreated=stats.recreated,
            appended=appended,
            errors=list(stats.errors),
            conflicts=list(stats.conflicts),
            state_changed=list(stats.state_changed),
            succeeded=set(stats.succeeded),
        )
    filtered = [
        result
        for result in stats.item_results
        if playlist_name in result.playlists
    ]
    subset = ConvertStats(
        item_results=filtered,
        succeeded=set(stats.succeeded),
        appended=appended,
    )
    return apply_item_result_aggregates(subset)


def conversion_exit_code(stats: ConvertStats) -> int:
    """Nonzero when any item failed, conflicted, or saw a state change."""
    if stats.item_results:
        if any(
            result.outcome in {"failed", "conflict", "state_changed"}
            for result in stats.item_results
        ):
            return 1
        return 0
    if stats.errors or stats.conflicts or stats.state_changed:
        return 1
    return 0


def conversion_report_title(
    stats: ConvertStats,
    *,
    cancelled: bool = False,
) -> str:
    """Done-dialog / finish title from batch outcomes."""
    if cancelled or any(r.outcome == "cancelled" for r in stats.item_results):
        return "Cancelled"
    if stats.item_results:
        successes = sum(1 for r in stats.item_results if r.outcome == "succeeded")
        has_problems = any(
            r.outcome in {"failed", "conflict", "state_changed"}
            for r in stats.item_results
        )
        has_failed = any(r.outcome == "failed" for r in stats.item_results)
    else:
        successes = (
            stats.converted
            + stats.copied
            + stats.pcm_rebuilt
            + stats.metadata_refreshed
            + stats.reused
            + stats.recreated
        )
        has_problems = bool(stats.errors or stats.conflicts or stats.state_changed)
        has_failed = bool(stats.errors)
    if successes == 0:
        if has_failed:
            return "Failed"
        return "No conversions"
    if has_problems:
        return "Partial"
    return "Done"


def _recreated_count_part(results: list[ItemResult]) -> str | None:
    rec_tx = rec_copy = 0
    for result in results:
        if result.outcome != "succeeded" or result.action != "recreate_missing":
            continue
        if result.write == "copy":
            rec_copy += 1
        else:
            rec_tx += 1
    total = rec_tx + rec_copy
    if not total:
        return None
    bits: list[str] = []
    if rec_tx:
        bits.append(f"{rec_tx} transcoded")
    if rec_copy:
        bits.append(f"{rec_copy} copied")
    if bits:
        return f"{total} recreated ({', '.join(bits)})"
    return f"{total} recreated"


def format_conversion_counts(
    stats: ConvertStats,
    *,
    missing: int = 0,
) -> list[str]:
    """User-facing count fragments shared by CLI summary and GUI finish."""
    results = stats.item_results
    if results:
        apply_item_result_aggregates(stats)
        parts: list[str] = []
        recreated = _recreated_count_part(results)
        if stats.converted:
            parts.append(f"{stats.converted} converted")
        if stats.copied:
            parts.append(f"{stats.copied} copied")
        if stats.pcm_rebuilt:
            parts.append(f"{stats.pcm_rebuilt} PCM-rebuilt")
        if stats.metadata_refreshed:
            parts.append(f"{stats.metadata_refreshed} metadata-refreshed")
        if stats.reused:
            parts.append(f"{stats.reused} reused")
        if recreated:
            parts.append(recreated)
        cancelled_n = sum(1 for r in results if r.outcome == "cancelled")
        if stats.skipped and not (stats.reused or stats.metadata_refreshed):
            parts.append(f"{stats.skipped} skipped")
        if cancelled_n:
            parts.append(f"{cancelled_n} cancelled")
        if stats.conflicts:
            n = len(stats.conflicts)
            parts.append(f"{n} conflict{'s' if n != 1 else ''}")
        if stats.state_changed:
            n = len(stats.state_changed)
            parts.append(f"{n} state-changed")
        if missing:
            parts.append(f"{missing} missing skipped")
        if stats.errors:
            n = len(stats.errors)
            parts.append(f"{n} failed")
        return parts
    parts = []
    if stats.converted:
        parts.append(f"{stats.converted} converted")
    if stats.copied:
        parts.append(f"{stats.copied} copied")
    if stats.pcm_rebuilt:
        parts.append(f"{stats.pcm_rebuilt} PCM-rebuilt")
    if stats.metadata_refreshed:
        parts.append(f"{stats.metadata_refreshed} metadata-refreshed")
    if stats.reused:
        parts.append(f"{stats.reused} reused")
    if stats.recreated:
        parts.append(f"{stats.recreated} recreated")
    if stats.skipped and not (stats.reused or stats.metadata_refreshed):
        parts.append(f"{stats.skipped} skipped")
    if stats.conflicts:
        n = len(stats.conflicts)
        parts.append(f"{n} conflict{'s' if n != 1 else ''}")
    if stats.state_changed:
        n = len(stats.state_changed)
        parts.append(f"{n} state-changed")
    if missing:
        parts.append(f"{missing} missing skipped")
    if stats.errors:
        n = len(stats.errors)
        parts.append(f"{n} failed")
    return parts


@dataclass
class PreparedConversion:
    """In-memory prepare result held until the user confirms or the CLI writes."""

    plans: list[Plan]
    items: list[PlannedTrack]
    manifest: ConverterManifest
    preview: ConversionPreview
    library_dir: Path
    output: Path
    skipped: list[str]
    decisions: dict[tuple[str, str], Decision] = field(default_factory=dict)
