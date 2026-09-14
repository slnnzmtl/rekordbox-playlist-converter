"""Write port: convert unique tracks and apply import XML for a prepared batch."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
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
    ItemResult,
    Plan,
    PlannedTrack,
    PreparedConversion,
)
from convert.progress import Progress
from convert.paths import source_key
from convert.quality import coerce_output_format
from convert.rerun import Decision, classify_item, file_snapshot, snapshots_match

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
        return prepared.decisions
    out: dict[tuple[str, str], Decision] = {}
    plan_by_item: dict[int, Plan] = {}
    for plan in prepared.plans:
        for item in plan.unique:
            plan_by_item.setdefault(id(item), plan)
    for item in prepared.items:
        fmt = coerce_output_format(item.output_format)
        key = (source_key(item.source_path), fmt)
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


class ManifestCheckpoint:
    """Persist complete in-memory records, overlaying incomplete on pending keys."""

    def __init__(
        self,
        plan: Plan,
        pending: set[tuple[str, str]],
        *,
        every: int | None,
    ) -> None:
        self.plan = plan
        self.pending = pending
        self.every = every
        self._count = 0

    def mark_item_complete(self, item: PlannedTrack) -> None:
        if self.plan.manifest is None:
            return
        fmt = coerce_output_format(item.output_format)
        record = self.plan.manifest.tracks.get(source_key(item.source_path), {}).get(
            fmt
        )
        if record is None:
            return
        mark_complete(
            record,
            source=source_signature(item.source_path),
            metadata=metadata_signature(item.source_el),
            output=output_signature(item.dest_path),
            recipe=recipe_from_item(item),
        )
        self.pending.discard((source_key(item.source_path), fmt))
        if not self.every:
            return
        self._count += 1
        if self._count % self.every == 0:
            _save_manifest_with_pending(
                self.plan.manifest, self.plan.library_dir, self.pending
            )

    def discard_pending_and_save(self, item: PlannedTrack) -> None:
        """Conflict/state_changed recovery: always persist the complete record."""
        fmt = coerce_output_format(item.output_format)
        self.pending.discard((source_key(item.source_path), fmt))
        if self.plan.manifest is None:
            return
        _save_manifest_with_pending(
            self.plan.manifest, self.plan.library_dir, self.pending
        )


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


@dataclass
class ExecuteContext:
    """Shared convert_unique state for one item execution."""

    plan: Plan
    force: bool
    cancel_event: threading.Event | None
    cover_lock: threading.Lock
    stats: ConvertStats
    stats_lock: threading.Lock
    checkpoint: ManifestCheckpoint
    finish: Callable[[str, str], None]

    def mark_succeeded(self, item: PlannedTrack) -> None:
        fmt = coerce_output_format(item.output_format)
        with self.stats_lock:
            self.stats.succeeded.add((source_key(item.source_path), fmt))


def _item_result(
    item: PlannedTrack,
    action: str,
    outcome: str,
    *,
    error: str | None = None,
    write: str = "",
) -> ItemResult:
    return ItemResult(
        source=item.source_path,
        destination=item.dest_path,
        action=action,
        outcome=outcome,
        error=error,
        write=write,
    )


def _track_ref(item: PlannedTrack) -> str:
    return f"{item.source_path.name} → {item.dest_name}"


def _snapshot_blocks_write(
    item: PlannedTrack, decision: Decision, ctx: ExecuteContext
) -> ItemResult | None:
    """Return a blocking result if frozen stats no longer match."""
    name = item.dest_name
    ref = _track_ref(item)
    key_src = decision.source_stat
    if key_src is not None:
        current_src = file_snapshot(item.source_path)
        if current_src is None or not snapshots_match(key_src, current_src):
            with ctx.stats_lock:
                ctx.checkpoint.discard_pending_and_save(item)
                ctx.stats.state_changed.append(ref)
            ctx.finish("state_changed", name)
            return _item_result(item, decision.action, "state_changed")
    frozen_dest = decision.dest_stat
    current_dest = file_snapshot(item.dest_path)
    dest_changed = (frozen_dest is None and current_dest is not None) or (
        frozen_dest is not None and not snapshots_match(frozen_dest, current_dest)
    )
    if dest_changed:
        with ctx.stats_lock:
            ctx.checkpoint.discard_pending_and_save(item)
            ctx.stats.conflicts.append(ref)
        ctx.finish("conflict", name)
        return _item_result(item, decision.action, "conflict")
    return None


def execute_item(
    item: PlannedTrack,
    decision: Decision | None,
    ctx: ExecuteContext,
) -> ItemResult:
    """Run one planned item using a frozen decision, or classify if none."""
    if ctx.cancel_event is not None and ctx.cancel_event.is_set():
        return _item_result(item, "cancelled", "cancelled")
    name = item.dest_name
    is_aiff = coerce_output_format(item.output_format) == "aiff"
    if decision is not None:
        blocked = _snapshot_blocks_write(item, decision, ctx)
        if blocked is not None:
            return blocked
        action = decision.action
    else:
        action = format_policy.planned_action(
            ctx.plan,
            item,
            ctx.force,
            cover_lock=ctx.cover_lock,
            cancel_event=ctx.cancel_event,
        )
    if action in {"reuse", "in_place_noop", "refresh_xml"}:
        if action == "refresh_xml":
            with ctx.stats_lock:
                ctx.checkpoint.mark_item_complete(item)
                ctx.stats.metadata_refreshed += 1
        else:
            with ctx.stats_lock:
                ctx.stats.reused += 1
        with ctx.stats_lock:
            ctx.stats.skipped += 1
        ctx.mark_succeeded(item)
        ctx.finish("skip", name)
        return _item_result(item, action, "succeeded")
    if action == "external_modification_conflict":
        with ctx.stats_lock:
            ctx.checkpoint.discard_pending_and_save(item)
            ctx.stats.conflicts.append(_track_ref(item))
        ctx.finish("conflict", name)
        return _item_result(item, action, "conflict")
    try:
        converter_manifest.ensure_dest_path_under_wav_dir(
            ctx.plan.library_dir, item.dest_path
        )
        if action == "update_metadata":
            cover = plan_module.cached_cover_jpeg(
                item.source_path,
                ctx.plan.cover_cache,
                lock=ctx.cover_lock,
                cancel_event=ctx.cancel_event,
            )
            write_aiff_id3(
                item.dest_path,
                item.source_el,
                cover,
                bit_depth=item.bit_depth,
                sample_rate=item.sample_rate,
            )
            with ctx.stats_lock:
                ctx.checkpoint.mark_item_complete(item)
                ctx.stats.metadata_refreshed += 1
                ctx.stats.skipped += 1
            ctx.mark_succeeded(item)
            ctx.finish("copy", name)
            return _item_result(item, action, "succeeded", write="copy")
        if action == "rewrite_container" and not is_aiff:
            pcm_src = _pcm_origin_for_container_rewrite(ctx.plan, item, ctx.force)
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
                with ctx.stats_lock:
                    ctx.stats.pcm_rebuilt += 1
                    ctx.stats.copied += 1
                    ctx.checkpoint.mark_item_complete(item)
                ctx.mark_succeeded(item)
                ctx.finish("copy", name)
                return _item_result(
                    item, "rewrite_container", "succeeded", write="copy"
                )
        if action == "rewrite_container" and is_aiff:
            cover = plan_module.cached_cover_jpeg(
                item.source_path,
                ctx.plan.cover_cache,
                lock=ctx.cover_lock,
                cancel_event=ctx.cancel_event,
            )

            def write_aiff_sidecar(sidecar: Path) -> None:
                pcm_src = _pcm_origin_for_container_rewrite(
                    ctx.plan, item, ctx.force
                )
                normalize_aiff_audio_chunks(pcm_src, sidecar)
                write_aiff_id3(
                    sidecar,
                    item.source_el,
                    cover,
                    bit_depth=item.bit_depth,
                    sample_rate=item.sample_rate,
                )

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
                with ctx.stats_lock:
                    ctx.stats.pcm_rebuilt += 1
                    ctx.stats.copied += 1
                    ctx.checkpoint.mark_item_complete(item)
                ctx.mark_succeeded(item)
                ctx.finish("copy", name)
                return _item_result(
                    item, "rewrite_container", "succeeded", write="copy"
                )
        if is_aiff:
            plan_module.write_aiff_output(
                item.source_path,
                item.dest_path,
                item.source_el,
                passthrough=item.passthrough,
                codec=item.codec,
                bit_depth=item.bit_depth,
                sample_rate=item.sample_rate,
                cover_cache=ctx.plan.cover_cache,
                cancel_event=ctx.cancel_event,
                cover_lock=ctx.cover_lock,
            )
            with ctx.stats_lock:
                if item.passthrough:
                    ctx.stats.copied += 1
                else:
                    ctx.stats.converted += 1
                if action == "recreate_missing":
                    ctx.stats.recreated += 1
                ctx.checkpoint.mark_item_complete(item)
            ctx.mark_succeeded(item)
            write = "copy" if item.passthrough else "transcode"
            ctx.finish("copy" if item.passthrough else "convert", name)
            return _item_result(item, action, "succeeded", write=write)
        if item.passthrough:
            copy_wav_atomic(
                item.source_path, item.dest_path, cancel_event=ctx.cancel_event
            )
            with ctx.stats_lock:
                ctx.stats.copied += 1
                if action == "recreate_missing":
                    ctx.stats.recreated += 1
                ctx.checkpoint.mark_item_complete(item)
            ctx.mark_succeeded(item)
            ctx.finish("copy", name)
            return _item_result(item, action, "succeeded", write="copy")
        if not item.codec:
            raise CliError(f"no codec planned for {item.source_path}")
        plan_module.run_ffmpeg(
            item.source_path,
            item.dest_path,
            item.codec,
            force=True,
            sample_rate=item.sample_rate,
            bit_depth=item.bit_depth,
            cancel_event=ctx.cancel_event,
            output_format=item.output_format,
        )
        with ctx.stats_lock:
            ctx.stats.converted += 1
            if action == "recreate_missing":
                ctx.stats.recreated += 1
            ctx.checkpoint.mark_item_complete(item)
        ctx.mark_succeeded(item)
        ctx.finish("convert", name)
        return _item_result(item, action, "succeeded", write="transcode")
    except CancelledError:
        return _item_result(item, action, "cancelled")
    except Exception as exc:  # noqa: BLE001 — collect all; report after pool
        message = f"{_track_ref(item)}: {exc}"
        with ctx.stats_lock:
            ctx.stats.errors.append(message)
        ctx.finish("error", name)
        return _item_result(item, action, "failed", error=message)


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
    pending = pending_assignments if pending_assignments is not None else set()
    checkpoint = ManifestCheckpoint(plan, pending, every=checkpoint_every)

    def finish(action: str, name: str) -> None:
        nonlocal completed
        with stats_lock:
            completed += 1
            done = completed
        bar.update(done, action, name)

    ctx = ExecuteContext(
        plan=plan,
        force=force,
        cancel_event=cancel_event,
        cover_lock=cover_lock,
        stats=stats,
        stats_lock=stats_lock,
        checkpoint=checkpoint,
        finish=finish,
    )

    def run_item(item: PlannedTrack) -> ItemResult:
        fmt = coerce_output_format(item.output_format)
        key = (source_key(item.source_path), fmt)
        frozen = decisions.get(key) if decisions else None
        result = execute_item(item, frozen, ctx)
        with stats_lock:
            stats.item_results.append(result)
        return result

    try:
        if not items:
            return stats
        effective_workers = plan_module.convert_worker_count(
            len(items), workers=workers
        )
        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = [pool.submit(run_item, item) for item in items]
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
    playlists_by_dest: dict[Path, list[str]] = {}
    for one_plan in plans:
        for track in one_plan.tracks:
            names = playlists_by_dest.setdefault(track.dest_path, [])
            if one_plan.wav_playlist_name not in names:
                names.append(one_plan.wav_playlist_name)
    if playlists_by_dest and stats.item_results:
        stats.item_results = [
            ItemResult(
                source=result.source,
                destination=result.destination,
                action=result.action,
                outcome=result.outcome,
                playlists=tuple(playlists_by_dest.get(result.destination, ())),
                error=result.error,
                write=result.write,
            )
            for result in stats.item_results
        ]
    _save_manifest_with_pending(prepared.manifest, prepared.library_dir, pending)
    appended_by_plan: list[int] = []
    for one_plan in plans:
        appended_by_plan.append(xml_output.apply_xml(one_plan, stats.succeeded))
    xml_output.write_import_xml(plans[0].output_root, plans[0].output)
    stats.appended_by_plan = appended_by_plan
    stats.appended = sum(appended_by_plan)
    return stats
