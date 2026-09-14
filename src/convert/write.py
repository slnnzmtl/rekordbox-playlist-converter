"""Write port: convert unique tracks and apply import XML for a prepared batch."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Callable

import converter_manifest
import xml_output
from cdj_aiff import (
    is_canonical_aiff_output,
    normalize_aiff_audio_chunks,
    write_aiff_id3,
)
from cdj_wav import is_cdj_safe_wav, rewrite_wav_pcm
from cli_error import CancelledError, CliError
from convert.encode import copy_wav_atomic
from convert.freshness import (
    assignment_state,
    mark_complete,
    mark_incomplete,
    metadata_signature,
    output_signature,
    recipe_from_item,
    source_signature,
)
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
from convert.rerun import Decision, classify_item, _file_stat, _stats_match

_MUTATING_ACTIONS = frozenset(
    {
        "transcode",
        "rewrite_container",
        "update_metadata",
        "recreate_missing",
    }
)


def _decisions_for_prepared(
    prepared: PreparedConversion, force: bool
) -> dict[tuple[str, str], Decision]:
    if prepared.decisions:
        return {
            key: value
            for key, value in prepared.decisions.items()
            if isinstance(value, Decision)
        }
    by_rel = {row.relative_dest: row for row in prepared.preview.items}
    plan_by_item: dict[int, Plan] = {}
    for plan in prepared.plans:
        for item in plan.unique:
            plan_by_item.setdefault(id(item), plan)
    out: dict[tuple[str, str], Decision] = {}
    for item in prepared.items:
        fmt = coerce_output_format(item.output_format)
        key = (source_key(item.source_path), fmt)
        try:
            rel = item.dest_path.relative_to(prepared.library_dir).as_posix()
        except ValueError:
            rel = item.dest_path.name
        row = by_rel.get(rel)
        if row is not None and row.action:
            out[key] = Decision(
                action=row.action,
                reason=row.reason_code or "",
                write_kind=row.write_kind or "none",
                source_stat=row.source_stat,
                dest_stat=row.dest_stat,
            )
            continue
        plan = plan_by_item.get(id(item), prepared.plans[0])
        out[key] = classify_item(plan, item, force)
    return out


def _replace_via_sidecar(
    dest: Path,
    write: Callable[[Path], None],
    *,
    validate: Callable[[Path], bool] | None = None,
) -> None:
    sidecar = dest.with_name(dest.name + "~")
    try:
        write(sidecar)
        if validate is not None and not validate(sidecar):
            raise CliError(f"sidecar failed validation for {dest}")
        os.replace(sidecar, dest)
    except Exception:
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _pcm_origin_for_container_rewrite(
    plan: Plan, item: PlannedTrack, force: bool
) -> Path:
    """Dest PCM only for a complete, unforced, source-matching revision rewrite."""
    if force:
        return item.source_path
    if plan.manifest is None:
        return item.source_path
    fmt = coerce_output_format(item.output_format)
    record = plan.manifest.tracks.get(source_key(item.source_path), {}).get(fmt) or {}
    if assignment_state(record) != "complete":
        return item.source_path
    stored = record.get("source") or {}
    try:
        st = item.source_path.stat()
    except OSError:
        return item.source_path
    if stored.get("size") != st.st_size or stored.get("mtime_ns") != st.st_mtime_ns:
        return item.source_path
    return item.dest_path


def _mutating_assignment_keys(
    prepared: PreparedConversion,
    force: bool,
    decisions: dict[tuple[str, str], Decision] | None = None,
) -> set[tuple[str, str]]:
    """Source/format keys that this batch will mutate and already have records."""
    keys: set[tuple[str, str]] = set()
    if not prepared.plans:
        return keys
    frozen = decisions if decisions is not None else _decisions_for_prepared(
        prepared, force
    )
    for item in prepared.items:
        fmt = coerce_output_format(item.output_format)
        key = (source_key(item.source_path), fmt)
        decision = frozen.get(key)
        action = (
            decision.action
            if decision is not None
            else format_policy.planned_action(prepared.plans[0], item, force)
        )
        if action not in _MUTATING_ACTIONS:
            continue
        record = prepared.manifest.tracks.get(key[0], {}).get(fmt)
        if record is None:
            continue
        keys.add(key)
    return keys


def _save_manifest_with_pending(
    manifest: converter_manifest.ConverterManifest,
    library_dir: Path,
    pending: set[tuple[str, str]],
) -> None:
    """Persist in-memory records, overlaying incomplete on unfinished mutations."""
    clone = converter_manifest.ConverterManifest(tracks=deepcopy(manifest.tracks))
    for key, fmt in pending:
        record = clone.tracks.get(key, {}).get(fmt)
        if record is None:
            continue
        mark_incomplete(record)
    converter_manifest.save_manifest(clone, library_dir)


def _prebatch_incomplete_manifest(
    prepared: PreparedConversion,
    force: bool,
    pending: set[tuple[str, str]] | None = None,
) -> converter_manifest.ConverterManifest:
    """Copy of the prepared manifest with planned mutations marked incomplete."""
    clone = converter_manifest.ConverterManifest(
        tracks=deepcopy(prepared.manifest.tracks)
    )
    keys = pending if pending is not None else _mutating_assignment_keys(
        prepared, force
    )
    for key, fmt in keys:
        record = clone.tracks.get(key, {}).get(fmt)
        if record is None:
            continue
        mark_incomplete(record)
    return clone


def convert_unique(
    plan: Plan,
    force: bool,
    *,
    progress: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    items: list[PlannedTrack] | None = None,
    workers: int | None = None,
    checkpoint_every: int | None = None,
    pending_assignments: set[tuple[str, str]] | None = None,
    decisions: dict[tuple[str, str], Decision] | None = None,
) -> ConvertStats:
    """Encode/copy/reuse unique planned tracks; wait in-flight on cancel."""
    stats = ConvertStats()
    plan.media_dir.mkdir(parents=True, exist_ok=True)
    items = list(items) if items is not None else plan.unique
    bar = Progress(len(items), progress, on_progress=on_progress)
    completed = 0
    stats_lock = threading.Lock()
    cover_lock = threading.Lock()
    checkpointed = 0
    pending = pending_assignments if pending_assignments is not None else set()

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

    def complete_assignment(item: PlannedTrack) -> None:
        nonlocal checkpointed
        if plan.manifest is None:
            return
        fmt = coerce_output_format(item.output_format)
        record = plan.manifest.tracks.get(source_key(item.source_path), {}).get(fmt)
        if record is None:
            return
        mark_complete(
            record,
            source=source_signature(item.source_path),
            metadata=metadata_signature(item.source_el),
            output=output_signature(item.dest_path),
            recipe=recipe_from_item(item),
        )
        pending.discard((source_key(item.source_path), fmt))
        if not checkpoint_every:
            return
        checkpointed += 1
        if checkpointed % checkpoint_every == 0:
            _save_manifest_with_pending(plan.manifest, plan.library_dir, pending)

    def clear_pending_checkpoint(item: PlannedTrack) -> None:
        """Drop pending incomplete overlay and checkpoint complete in-memory record."""
        nonlocal checkpointed
        fmt = coerce_output_format(item.output_format)
        pending.discard((source_key(item.source_path), fmt))
        if plan.manifest is None or not checkpoint_every:
            return
        checkpointed += 1
        _save_manifest_with_pending(plan.manifest, plan.library_dir, pending)

    def snapshot_blocks_write(
        item: PlannedTrack, decision: Decision, name: str
    ) -> bool:
        """Return True if frozen stats no longer match; records conflict/state_changed."""
        key_src = decision.source_stat
        if key_src is not None:
            current_src = _file_stat(item.source_path)
            if current_src is None or not _stats_match(key_src, current_src):
                with stats_lock:
                    clear_pending_checkpoint(item)
                    stats.state_changed.append(name)
                finish("state_changed", name)
                return True
        frozen_dest = decision.dest_stat
        current_dest = _file_stat(item.dest_path)
        dest_changed = (frozen_dest is None and current_dest is not None) or (
            frozen_dest is not None and not _stats_match(frozen_dest, current_dest)
        )
        if dest_changed:
            with stats_lock:
                clear_pending_checkpoint(item)
                stats.conflicts.append(name)
            finish("conflict", name)
            return True
        return False

    def process_one(item: PlannedTrack) -> None:
        if cancel_event is not None and cancel_event.is_set():
            return
        name = item.dest_name
        is_aiff = coerce_output_format(item.output_format) == "aiff"
        fmt = coerce_output_format(item.output_format)
        key = (source_key(item.source_path), fmt)
        decision = decisions.get(key) if decisions else None
        if decision is not None:
            if snapshot_blocks_write(item, decision, name):
                return
            action = decision.action
        else:
            action = format_policy.planned_action(
                plan, item, force, cover_lock=cover_lock, cancel_event=cancel_event
            )
        if action in {"reuse", "in_place_noop", "refresh_xml"}:
            if action == "refresh_xml":
                with stats_lock:
                    complete_assignment(item)
            with stats_lock:
                stats.skipped += 1
            mark_succeeded(item)
            finish("skip", name)
            return
        if action == "external_modification_conflict":
            with stats_lock:
                clear_pending_checkpoint(item)
                stats.conflicts.append(name)
            finish("conflict", name)
            return
        try:
            converter_manifest.ensure_dest_path_under_wav_dir(
                plan.library_dir, item.dest_path
            )
            if action == "update_metadata":
                cover = plan_module.cached_cover_jpeg(
                    item.source_path,
                    plan.cover_cache,
                    lock=cover_lock,
                    cancel_event=cancel_event,
                )
                write_aiff_id3(item.dest_path, item.source_el, cover)
                with stats_lock:
                    complete_assignment(item)
                mark_succeeded(item)
                finish("copy", name)
                return
            if action == "rewrite_container" and not is_aiff:
                pcm_src = _pcm_origin_for_container_rewrite(plan, item, force)
                try:
                    _replace_via_sidecar(
                        item.dest_path,
                        lambda sidecar, src=pcm_src: rewrite_wav_pcm(src, sidecar),
                        validate=lambda path: is_cdj_safe_wav(
                            path,
                            bit_depth=item.bit_depth,
                            sample_rate=item.sample_rate,
                        ),
                    )
                except CliError:
                    action = "transcode"
                else:
                    with stats_lock:
                        stats.copied += 1
                        complete_assignment(item)
                    mark_succeeded(item)
                    finish("copy", name)
                    return
            if action == "rewrite_container" and is_aiff:
                cover = plan_module.cached_cover_jpeg(
                    item.source_path,
                    plan.cover_cache,
                    lock=cover_lock,
                    cancel_event=cancel_event,
                )

                def write_aiff_sidecar(sidecar: Path) -> None:
                    pcm_src = _pcm_origin_for_container_rewrite(plan, item, force)
                    normalize_aiff_audio_chunks(pcm_src, sidecar)
                    write_aiff_id3(sidecar, item.source_el, cover)

                try:
                    _replace_via_sidecar(
                        item.dest_path,
                        write_aiff_sidecar,
                        validate=lambda path: is_canonical_aiff_output(
                            path,
                            item.source_el,
                            cover,
                            bit_depth=item.bit_depth,
                            sample_rate=item.sample_rate,
                        ),
                    )
                except CliError:
                    action = "transcode"
                else:
                    with stats_lock:
                        stats.copied += 1
                        complete_assignment(item)
                    mark_succeeded(item)
                    finish("copy", name)
                    return
            if is_aiff:
                plan_module.write_aiff_output(
                    item.source_path,
                    item.dest_path,
                    item.source_el,
                    passthrough=item.passthrough,
                    codec=item.codec,
                    bit_depth=item.bit_depth,
                    sample_rate=item.sample_rate,
                    cover_cache=plan.cover_cache,
                    cancel_event=cancel_event,
                    cover_lock=cover_lock,
                )
                with stats_lock:
                    if item.passthrough:
                        stats.copied += 1
                    else:
                        stats.converted += 1
                    complete_assignment(item)
                mark_succeeded(item)
                finish("copy" if item.passthrough else "convert", name)
                return
            if item.passthrough:
                copy_wav_atomic(
                    item.source_path, item.dest_path, cancel_event=cancel_event
                )
                with stats_lock:
                    stats.copied += 1
                    complete_assignment(item)
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
                complete_assignment(item)
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
    checkpoint_every: int = 8,
) -> ConvertStats:
    """Save manifest, convert unique items, apply XML per plan, write import XML.

    Encode cancel waits in-flight; successes still receive apply_xml + write.
    Hosts map cancel vs errors vs ok from the returned stats and cancel_event.
    """
    decisions = _decisions_for_prepared(prepared, force)
    prepared.decisions = dict(decisions)
    pending = _mutating_assignment_keys(prepared, force, decisions)
    converter_manifest.save_manifest(
        _prebatch_incomplete_manifest(prepared, force, pending),
        prepared.library_dir,
    )
    plans = prepared.plans
    stats = convert_unique(
        plans[0],
        force=force,
        progress=progress,
        on_progress=on_progress,
        cancel_event=cancel_event,
        items=prepared.items,
        workers=workers,
        checkpoint_every=checkpoint_every,
        pending_assignments=pending,
        decisions=decisions,
    )
    _save_manifest_with_pending(prepared.manifest, prepared.library_dir, pending)
    appended_by_plan: list[int] = []
    for one_plan in plans:
        appended_by_plan.append(xml_output.apply_xml(one_plan, stats.succeeded))
    xml_output.write_import_xml(plans[0].output_root, plans[0].output)
    stats.appended_by_plan = appended_by_plan
    stats.appended = sum(appended_by_plan)
    return stats
