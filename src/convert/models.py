"""Convert plan dataclasses and progress bar."""

from __future__ import annotations

import shutil
import sys
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from converter_manifest import ConverterManifest


class Progress:
    """Single-line stderr bar. Callback always fires; stderr only when enabled."""

    def __init__(
        self,
        total: int,
        enabled: bool,
        on_progress: Callable[[int, int, str, str], None] | None = None,
    ) -> None:
        self.total = max(total, 0)
        self.enabled = enabled
        self.on_progress = on_progress
        self._width = 0
        self._lock = threading.Lock()

    def update(self, current: int, action: str, name: str) -> None:
        with self._lock:
            if self.on_progress is not None:
                self.on_progress(current, self.total, action, name)
            if not self.enabled:
                return
            total = self.total
            frac = 1.0 if total == 0 else min(current / total, 1.0)
            bar_w = 24
            filled = int(bar_w * frac) if total else bar_w
            bar = "#" * filled + "-" * (bar_w - filled)
            denom = total if total else current
            label = f"[{bar}] {current}/{denom}  {action}  {name}"
            cols = shutil.get_terminal_size((80, 24)).columns
            if cols > 8 and len(label) > cols - 1:
                label = label[: cols - 2] + "…"
            pad = max(self._width - len(label), 0)
            sys.stderr.write("\r" + label + (" " * pad))
            sys.stderr.flush()
            self._width = len(label)

    def close(self) -> None:
        with self._lock:
            if not self.enabled:
                return
            sys.stderr.write("\n")
            sys.stderr.flush()
            self.enabled = False


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
