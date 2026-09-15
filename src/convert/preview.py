"""Conversion preview and output disk-space checks."""

from __future__ import annotations

import math
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cli_error import CancelledError
from convert import plan as plan_module
from convert.models import ConversionPreview, ConversionPreviewItem, Plan, PlannedTrack
from convert.rerun import (
    ACTION_WRITE_KIND,
    WRITE_KIND_LABELS,
    Decision,
    classify_item,
    preview_action_label,
    preview_reason,
)
from convert.paths import _format_size_mb, source_key
from convert.quality import coerce_output_format

_BIT_DEPTH_LABELS = {"16": "16-bit", "24": "24-bit"}
_SAMPLE_RATE_LABELS = {"44100": "44.1 kHz", "48000": "48 kHz"}


@dataclass(frozen=True)
class PreviewRow:
    """User-facing fields for one unique-output preview line (GUI + CLI)."""

    source_display: str
    relative_dest: str
    output_format: str
    action_label: str
    reason: str
    write_kind_label: str
    quality: str
    size_display: str


def format_preview_summary(preview: ConversionPreview) -> str:
    """Shared summary line for GUI preview dialog and CLI --dry-run."""
    return (
        f"{preview.unique_outputs} unique output file(s) · "
        f"{preview.selected} selected · "
        f"{preview.resolved} resolved · "
        f"{preview.duplicates} duplicate(s) · "
        f"{preview.missing} missing"
    )


def format_preview_row(item: ConversionPreviewItem) -> PreviewRow:
    """User-facing labels for one ConversionPreviewItem."""
    fmt = coerce_output_format(item.output_format or "wav")
    depth = _BIT_DEPTH_LABELS.get(str(item.bit_depth), f"{item.bit_depth}-bit")
    rate = _SAMPLE_RATE_LABELS.get(
        str(item.sample_rate), f"{item.sample_rate} Hz"
    )
    write_kind = item.write_kind or ACTION_WRITE_KIND.get(item.action, "none")
    return PreviewRow(
        source_display=item.source_display,
        relative_dest=item.relative_dest,
        output_format=fmt.upper(),
        action_label=preview_action_label(item.action, item.reason_code),
        reason=item.reason,
        write_kind_label=WRITE_KIND_LABELS.get(write_kind, write_kind),
        quality=f"{depth} / {rate}",
        size_display=item.size_display,
    )


def preview_size(item: PlannedTrack, write_kind: str) -> tuple[int | None, str]:
    """Return (bytes, display). Audio estimates PCM; other kinds use dest size or —."""
    if write_kind != "audio":
        try:
            if item.dest_path.is_file():
                size = item.dest_path.stat().st_size
                return size, _format_size_mb(size)
        except OSError:
            pass
        return None, "—"
    duration = item.duration_seconds
    if duration is None or not math.isfinite(duration) or duration < 0:
        return None, "—"
    bytes_per_sample = 2 if item.bit_depth == 16 else 3
    estimated = int(duration * item.sample_rate * bytes_per_sample * 2)
    return estimated, _format_size_mb(estimated, approximate=True)


def build_conversion_preview(
    plans: list[Plan],
    items: list[PlannedTrack],
    force: bool,
    *,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    workers: int | None = None,
) -> ConversionPreview:
    """Build a read-only conversion preview from prepared plans and unique items."""
    if not plans:
        return ConversionPreview(
            selected=0,
            resolved=0,
            unique_outputs=0,
            duplicates=0,
            missing=0,
            items=[],
        )
    resolved = sum(len(plan.tracks) for plan in plans)
    missing = sum(len(plan.warnings) for plan in plans)
    selected = resolved + missing
    unique_outputs = len(items)
    duplicates = resolved - unique_outputs
    library_dir = plans[0].library_dir
    total = len(items)
    if total == 0:
        return ConversionPreview(
            selected=selected,
            resolved=resolved,
            unique_outputs=unique_outputs,
            duplicates=duplicates,
            missing=missing,
            items=[],
        )

    plan_by_item: dict[int, Plan] = {}
    for plan in plans:
        for item in plan.unique:
            plan_by_item.setdefault(id(item), plan)

    results: list[ConversionPreviewItem | None] = [None] * total
    progress_lock = threading.Lock()
    completed = 0
    decisions: dict[tuple[str, str], Decision] = {}

    def classify_one(index: int, item: PlannedTrack) -> ConversionPreviewItem:
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("conversion cancelled during preview")
        plan = plan_by_item.get(id(item), plans[0])
        decision = classify_item(plan, item, force)
        fmt = coerce_output_format(item.output_format)
        key = (source_key(item.source_path), fmt)
        with progress_lock:
            decisions[key] = decision
        try:
            relative_dest = item.dest_path.relative_to(library_dir).as_posix()
        except ValueError:
            relative_dest = item.dest_path.name
        size_bytes, size_display = preview_size(item, decision.write_kind)
        return ConversionPreviewItem(
            relative_dest=relative_dest,
            action=decision.action,
            bit_depth=item.bit_depth,
            sample_rate=item.sample_rate,
            size_bytes=size_bytes,
            size_display=size_display,
            source_display=item.source_path.name,
            reason=preview_reason(decision.action, decision.reason),
            write_kind=decision.write_kind,
            reason_code=decision.reason,
            source_stat=decision.source_stat,
            dest_stat=decision.dest_stat,
            output_format=fmt,
        )

    workers = plan_module.convert_worker_count(total, workers=workers)
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            pool.submit(classify_one, index, item): index
            for index, item in enumerate(items)
        }
        for fut in as_completed(futures):
            if cancel_event is not None and cancel_event.is_set():
                break
            index = futures[fut]
            try:
                results[index] = fut.result()
            except CancelledError:
                if cancel_event is not None:
                    cancel_event.set()
                break
            with progress_lock:
                completed += 1
                done = completed
            if on_progress is not None:
                on_progress(done, total, "preview", items[index].dest_name)
    finally:
        plan_module._shutdown_cancelable_pool(pool, cancel_event)
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("conversion cancelled during preview")

    preview_items = [item for item in results if item is not None]
    if len(preview_items) != total:
        raise CancelledError("conversion cancelled during preview")
    return ConversionPreview(
        selected=selected,
        resolved=resolved,
        unique_outputs=unique_outputs,
        duplicates=duplicates,
        missing=missing,
        items=preview_items,
        decisions=decisions,
    )


def preview_write_bytes(preview: ConversionPreview) -> int:
    """Bytes that audio writes will consume; metadata/none need no extra space."""
    total = 0
    for item in preview.items:
        write_kind = item.write_kind or ACTION_WRITE_KIND.get(item.action, "none")
        if write_kind != "audio":
            continue
        if item.size_bytes is None:
            continue
        total += item.size_bytes
    return total


def _disk_usage_path(path: Path) -> Path:
    """Nearest existing ancestor suitable for shutil.disk_usage."""
    candidate = path.expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        pass
    while True:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            pass
        parent = candidate.parent
        if parent == candidate:
            return candidate
        candidate = parent


def insufficient_output_space_message(
    path: Path,
    required_bytes: int,
    *,
    disk_usage: Callable[[str | Path], object] | None = None,
) -> str | None:
    """Return an issue message when free space on path is below required_bytes."""
    if required_bytes <= 0:
        return None
    probe = disk_usage if disk_usage is not None else shutil.disk_usage
    try:
        usage = probe(_disk_usage_path(path))
        free = int(getattr(usage, "free"))
    except (OSError, TypeError, ValueError, AttributeError):
        return None
    if free >= required_bytes:
        return None
    needed = _format_size_mb(required_bytes, approximate=True)
    available = _format_size_mb(free)
    return (
        "Not enough free space in the output folder. "
        f"About {needed.removeprefix('≈ ')} needed, {available} available."
    )


def preview_block_message(
    preview: ConversionPreview,
    path: Path,
    *,
    disk_usage: Callable[[str | Path], object] | None = None,
) -> str | None:
    """Block confirm when conflicts remain or the output volume is too small."""
    n = sum(
        1
        for item in preview.items
        if item.action == "external_modification_conflict"
    )
    if n:
        noun = "conflict" if n == 1 else "conflicts"
        return (
            f"{n} unresolved {noun}. "
            "Destination files were changed outside this app."
        )
    return insufficient_output_space_message(
        path,
        preview_write_bytes(preview),
        disk_usage=disk_usage,
    )
