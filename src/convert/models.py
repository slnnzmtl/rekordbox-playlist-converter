"""Convert plan dataclasses."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from converter_manifest import ConverterManifest


@dataclass
class PlannedTrack:
    source_el: ET.Element
    source_path: Path
    dest_path: Path
    dest_location: str
    dest_name: str
    codec: str | None  # None means copy WAV (or no-op)
    copy_wav: bool
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


@dataclass
class ConversionPreview:
    selected: int
    resolved: int
    unique_outputs: int
    duplicates: int
    missing: int
    items: list[ConversionPreviewItem] = field(default_factory=list)


@dataclass
class Plan:
    playlist_name: str
    wav_playlist_name: str
    wav_dir: Path
    playlist_dir: Path
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


@dataclass
class ConvertStats:
    converted: int = 0
    copied: int = 0
    skipped: int = 0
    appended: int = 0
    errors: list[str] = field(default_factory=list)
    # (source_key, format) that skipped, copied, or converted successfully.
    succeeded: set[tuple[str, str]] = field(default_factory=set)
    # Entries appended per plan by apply_xml during execute_prepared.
    appended_by_plan: list[int] = field(default_factory=list)


@dataclass
class PreparedConversion:
    """In-memory prepare result held until the user confirms or the CLI writes."""

    plans: list[Plan]
    items: list[PlannedTrack]
    manifest: ConverterManifest
    preview: ConversionPreview
    wav_dir: Path
    output: Path
    skipped: list[str]
