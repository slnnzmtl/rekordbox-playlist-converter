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
    write: str = ""


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
    # (source_key, format) that skipped, copied, or converted successfully.
    succeeded: set[tuple[str, str]] = field(default_factory=set)
    # Entries appended per plan by apply_xml during execute_prepared.
    appended_by_plan: list[int] = field(default_factory=list)


def format_conversion_counts(
    stats: ConvertStats,
    *,
    missing: int = 0,
) -> list[str]:
    """User-facing count fragments shared by CLI summary and GUI finish."""
    parts: list[str] = []
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
