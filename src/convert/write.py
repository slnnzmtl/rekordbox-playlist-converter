"""Write port: convert unique tracks and apply import XML for a prepared batch."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import converter_manifest
import xml_output
from cli_error import CancelledError, CliError
from convert.encode import copy_wav_atomic
from convert import format_policy
from convert import plan as plan_module
from convert.models import (
    ConvertStats,
    Plan,
    PlannedTrack,
    PreparedConversion,
)
from convert.progress import Progress
from convert.paths import source_key
from convert.quality import coerce_output_format


def convert_unique(
    plan: Plan,
    force: bool,
    *,
    progress: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    items: list[PlannedTrack] | None = None,
    workers: int | None = None,
) -> ConvertStats:
    """Encode/copy/reuse unique planned tracks; wait in-flight on cancel."""
    stats = ConvertStats()
    plan.playlist_dir.mkdir(parents=True, exist_ok=True)
    items = list(items) if items is not None else plan.unique
    bar = Progress(len(items), progress, on_progress=on_progress)
    completed = 0
    stats_lock = threading.Lock()
    cover_lock = threading.Lock()

    def finish(action: str, name: str) -> None:
        nonlocal completed
        with stats_lock:
            completed += 1
            done = completed
        bar.update(done, action, name)

    def mark_succeeded(item: PlannedTrack) -> None:
        fmt = coerce_output_format(item.output_format)
        with stats_lock:
            stats.succeeded.add((source_key(item.source_path), fmt))

    def process_one(item: PlannedTrack) -> None:
        if cancel_event is not None and cancel_event.is_set():
            return
        name = item.dest_name
        is_aiff = coerce_output_format(item.output_format) == "aiff"
        action = format_policy.planned_action(
            plan, item, force, cover_lock=cover_lock, cancel_event=cancel_event
        )
        if action == "reuse":
            with stats_lock:
                stats.skipped += 1
            mark_succeeded(item)
            finish("skip", name)
            return
        try:
            converter_manifest.ensure_dest_path_under_wav_dir(
                plan.wav_dir, item.dest_path
            )
            if is_aiff:
                plan_module.write_aiff_output(
                    item.source_path,
                    item.dest_path,
                    item.source_el,
                    passthrough=item.copy_wav,
                    codec=item.codec,
                    bit_depth=item.bit_depth,
                    sample_rate=item.sample_rate,
                    cover_cache=plan.cover_cache,
                    cancel_event=cancel_event,
                    cover_lock=cover_lock,
                )
                with stats_lock:
                    if item.copy_wav:
                        stats.copied += 1
                    else:
                        stats.converted += 1
                mark_succeeded(item)
                finish("copy" if item.copy_wav else "convert", name)
                return
            if item.copy_wav:
                copy_wav_atomic(
                    item.source_path, item.dest_path, cancel_event=cancel_event
                )
                with stats_lock:
                    stats.copied += 1
                mark_succeeded(item)
                finish("copy", name)
                return
            if not item.codec:
                raise CliError(f"no codec planned for {item.source_path}")
            plan_module.run_ffmpeg(
                item.source_path,
                item.dest_path,
                item.codec,
                force=True,
                sample_rate=item.sample_rate,
                bit_depth=item.bit_depth,
                cancel_event=cancel_event,
                output_format=item.output_format,
            )
            with stats_lock:
                stats.converted += 1
            mark_succeeded(item)
            finish("convert", name)
        except CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 — collect all; report after pool
            with stats_lock:
                stats.errors.append(str(exc))
            finish("error", name)

    try:
        if not items:
            return stats
        effective_workers = plan_module.convert_worker_count(
            len(items), workers=workers
        )
        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = [pool.submit(process_one, item) for item in items]
            for fut in as_completed(futures):
                fut.result()
    finally:
        bar.close()
    return stats


def execute_prepared(
    prepared: PreparedConversion,
    *,
    force: bool = False,
    progress: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    workers: int | None = None,
) -> ConvertStats:
    """Save manifest, convert unique items, apply XML per plan, write import XML.

    Encode cancel waits in-flight; successes still receive apply_xml + write.
    Hosts map cancel vs errors vs ok from the returned stats and cancel_event.
    """
    converter_manifest.save_manifest(prepared.manifest, prepared.wav_dir)
    plans = prepared.plans
    stats = convert_unique(
        plans[0],
        force=force,
        progress=progress,
        on_progress=on_progress,
        cancel_event=cancel_event,
        items=prepared.items,
        workers=workers,
    )
    appended_by_plan: list[int] = []
    for one_plan in plans:
        appended_by_plan.append(xml_output.apply_xml(one_plan, stats.succeeded))
    xml_output.write_import_xml(plans[0].output_root, plans[0].output)
    stats.appended_by_plan = appended_by_plan
    stats.appended = sum(appended_by_plan)
    return stats
