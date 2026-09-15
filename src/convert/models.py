"""Convert plan dataclasses."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from convert.rerun import Decision
    from converter_manifest import ConverterManifest, ReservationContext


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
    output_format: str = "wav"


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
    reason: str = ""  # classifier reason (e.g. not_converted, dest_missing)


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
    # Full PlaylistApplyResult per plan (same order as plans / appended_by_plan).
    playlist_results: list = field(default_factory=list)


def _item_ref(result: ItemResult) -> str:
    dest = result.destination
    dest_display = (
        f"{dest.parent.name}/{dest.name}" if dest.parent.name else dest.name
    )
    return f"{result.source.name} → {dest_display}"


def _item_error_line(result: ItemResult) -> str:
    ref = _item_ref(result)
    if result.error:
        return f"{ref}: {result.error}"
    return ref


@dataclass(frozen=True)
class ItemResultSummary:
    """Pure counts derived from ItemResult rows."""

    converted: int = 0
    copied: int = 0
    skipped: int = 0
    pcm_rebuilt: int = 0
    metadata_refreshed: int = 0
    reused: int = 0
    recreated: int = 0
    recreated_transcoded: int = 0
    recreated_copied: int = 0
    cancelled: int = 0
    errors: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    state_changed: tuple[str, ...] = ()
    successes: int = 0
    has_problems: bool = False
    has_failed: bool = False


def summarize_item_results(results: list[ItemResult]) -> ItemResultSummary:
    """Derive report counts from ItemResult without mutating ConvertStats."""
    converted = copied = pcm = meta = reused = skipped = 0
    rec_tx = rec_copy = cancelled = 0
    errors: list[str] = []
    conflicts: list[str] = []
    state_changed: list[str] = []
    successes = 0
    for result in results:
        if result.outcome == "failed":
            errors.append(_item_error_line(result))
            continue
        if result.outcome == "conflict":
            conflicts.append(_item_ref(result))
            continue
        if result.outcome == "state_changed":
            state_changed.append(_item_ref(result))
            continue
        if result.outcome == "cancelled":
            cancelled += 1
            continue
        if result.outcome != "succeeded":
            continue
        successes += 1
        if result.action == "recreate_missing":
            if result.reason == "not_converted":
                if result.write == "copy":
                    copied += 1
                else:
                    converted += 1
            elif result.write == "copy":
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
    return ItemResultSummary(
        converted=converted,
        copied=copied,
        skipped=skipped,
        pcm_rebuilt=pcm,
        metadata_refreshed=meta,
        reused=reused,
        recreated=rec_tx + rec_copy,
        recreated_transcoded=rec_tx,
        recreated_copied=rec_copy,
        cancelled=cancelled,
        errors=tuple(errors),
        conflicts=tuple(conflicts),
        state_changed=tuple(state_changed),
        successes=successes,
        has_problems=bool(errors or conflicts or state_changed),
        has_failed=bool(errors),
    )


def apply_item_result_aggregates(stats: ConvertStats) -> ConvertStats:
    """Fill ConvertStats counters from item_results. Keep succeeded/appended."""
    results = stats.item_results
    if not results:
        return stats
    summary = summarize_item_results(results)
    stats.converted = summary.converted
    stats.copied = summary.copied
    stats.pcm_rebuilt = summary.pcm_rebuilt
    stats.metadata_refreshed = summary.metadata_refreshed
    stats.reused = summary.reused
    stats.recreated = summary.recreated
    stats.skipped = summary.skipped
    stats.errors = list(summary.errors)
    stats.conflicts = list(summary.conflicts)
    stats.state_changed = list(summary.state_changed)
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
        summary = summarize_item_results(stats.item_results)
        return 1 if summary.has_problems else 0
    if stats.errors or stats.conflicts or stats.state_changed:
        return 1
    return 0


def conversion_report_title(
    stats: ConvertStats,
    *,
    cancelled: bool = False,
    missing: int = 0,
) -> str:
    """Done-dialog / finish title from batch outcomes."""
    if cancelled or any(r.outcome == "cancelled" for r in stats.item_results):
        return "Cancelled"
    if stats.item_results:
        summary = summarize_item_results(stats.item_results)
        successes = summary.successes
        has_problems = summary.has_problems
        has_failed = summary.has_failed
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
    incomplete_playlist = any(
        not bool(getattr(result, "fully_synced", True))
        for result in stats.playlist_results
    )
    if successes == 0:
        if has_failed:
            return "Failed"
        return "No conversions"
    if has_problems or incomplete_playlist or missing:
        return "Partial"
    return "Done"


def _recreated_count_part_from_summary(summary: ItemResultSummary) -> str | None:
    total = summary.recreated
    if not total:
        return None
    bits: list[str] = []
    if summary.recreated_transcoded:
        bits.append(f"{summary.recreated_transcoded} transcoded")
    if summary.recreated_copied:
        bits.append(f"{summary.recreated_copied} copied")
    if bits:
        return f"{total} recreated ({', '.join(bits)})"
    return f"{total} recreated"


def _recreated_count_part(results: list[ItemResult]) -> str | None:
    return _recreated_count_part_from_summary(summarize_item_results(results))


def format_conversion_counts(
    stats: ConvertStats,
    *,
    missing: int = 0,
) -> list[str]:
    """User-facing count fragments shared by CLI summary and GUI finish."""
    results = stats.item_results
    if results:
        summary = summarize_item_results(results)
        parts: list[str] = []
        recreated = _recreated_count_part_from_summary(summary)
        if summary.converted:
            parts.append(f"{summary.converted} converted")
        if summary.copied:
            parts.append(f"{summary.copied} copied")
        if summary.pcm_rebuilt:
            parts.append(f"{summary.pcm_rebuilt} PCM-rebuilt")
        if summary.metadata_refreshed:
            parts.append(f"{summary.metadata_refreshed} metadata-refreshed")
        if summary.reused:
            parts.append(f"{summary.reused} reused")
        if recreated:
            parts.append(recreated)
        if summary.skipped and not (summary.reused or summary.metadata_refreshed):
            parts.append(f"{summary.skipped} skipped")
        if summary.cancelled:
            parts.append(f"{summary.cancelled} cancelled")
        if summary.conflicts:
            n = len(summary.conflicts)
            parts.append(f"{n} conflict{'s' if n != 1 else ''}")
        if summary.state_changed:
            n = len(summary.state_changed)
            parts.append(f"{n} state-changed")
        if missing:
            parts.append(f"{missing} missing skipped")
        if summary.errors:
            n = len(summary.errors)
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


def format_playlist_report(
    playlist_name: str,
    playlist_result: object | None,
    *,
    count_parts: list[str],
    missing: list[str] | None = None,
) -> list[str]:
    """One playlist's finish lines (counts + sync status + missing paths)."""
    parts = list(count_parts)
    appended = int(getattr(playlist_result, "appended", 0) or 0) if playlist_result else 0
    removed = int(getattr(playlist_result, "removed", 0) or 0) if playlist_result else 0
    reordered = bool(getattr(playlist_result, "reordered", False)) if playlist_result else False
    fully_synced = (
        bool(getattr(playlist_result, "fully_synced", True))
        if playlist_result is not None
        else True
    )
    if playlist_result is not None and not fully_synced:
        parts.append("generated playlist was not created or refreshed")
    elif removed or reordered:
        parts.append("playlist refreshed")
    elif appended:
        parts.append(f"+{appended} playlist entries")
    if parts:
        lines = [f"{playlist_name}: {', '.join(parts)}"]
    else:
        lines = [playlist_name]
    if missing:
        lines.append("Missing skipped:")
        lines.extend(missing)
    return lines


def format_import_guidance(
    output: Path,
    *,
    output_format: str = "wav",
    playlists: list[tuple[str, object]] | None = None,
) -> str:
    """Rekordbox import steps; omit Import Playlist when no NODE was synced."""
    suffix = "[AIFF]" if output_format == "aiff" else "[WAV]"
    header = (
        "Import into Rekordbox:\n"
        "1. Preferences → View → Layout → enable rekordbox xml\n"
        "2. Preferences → Advanced → Database → Imported Library →\n"
        f"   {output}"
    )
    pairs = playlists or []
    synced = [
        name
        for name, result in pairs
        if bool(getattr(result, "fully_synced", True))
    ]
    if not pairs:
        return (
            f"{header}\n"
            "3. Browser → rekordbox xml → Playlists → Import Playlist\n"
            f"   (or drag the {suffix} playlist into Playlists)"
        )
    if not synced:
        return (
            f"{header}\n"
            "The generated playlist was not created or refreshed. "
            "Do not import a generated playlist until a complete run writes it."
        )
    if len(synced) == len(pairs):
        return (
            f"{header}\n"
            "3. Browser → rekordbox xml → Playlists → Import Playlist\n"
            f"   (or drag the {suffix} playlist into Playlists)"
        )
    names = ", ".join(synced)
    return (
        f"{header}\n"
        "3. Browser → rekordbox xml → Playlists → Import Playlist\n"
        f"   Safe to import: {names}"
    )


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
    reservation: ReservationContext | None = None
