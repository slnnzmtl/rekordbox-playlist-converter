"""Write port: convert unique tracks and apply import XML for a prepared batch."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

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
    apply_item_result_aggregates,
)
from convert.progress import Progress
from convert.paths import source_key
from convert.quality import coerce_output_format
from convert.rerun import (
    Decision,
    classify_item,
    container_rewrite_supported,
    file_snapshot,
    snapshots_match,
)

_MUTATING_ACTIONS = frozenset(
    {
        "transcode",
        "rewrite_container",
        "update_metadata",
        "recreate_missing",
    }
)


class ManifestPersistError(Exception):
    """Durable manifest write failed; abort before Import XML."""


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
    if container_rewrite_supported(item.dest_path, item.output_format) is not True:
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


def _overlay_tracks(
    manifest: converter_manifest.ConverterManifest,
    pending: set[tuple[str, str]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Deepcopy tracks with incomplete overlay on unfinished mutation keys."""
    tracks = deepcopy(manifest.tracks)
    for key, fmt in pending:
        record = tracks.get(key, {}).get(fmt)
        if record is None:
            continue
        mark_incomplete(record)
    return tracks


class ManifestPersister:
    """Serialize manifest saves with sequenced periodics and urgent/final syncs."""

    def __init__(
        self,
        library_dir: Path,
        *,
        checkpoint_interval_s: float | None = 10.0,
        clock: Callable[[], float] | None = None,
        save_tracks: Callable | None = None,
        before_persist: Callable[[], None] | None = None,
    ) -> None:
        self._library_dir = library_dir
        self._clock = clock if clock is not None else time.monotonic
        self._save_tracks = (
            save_tracks
            if save_tracks is not None
            else converter_manifest.save_manifest_tracks
        )
        self._before_persist = before_persist
        if checkpoint_interval_s is None or checkpoint_interval_s <= 0:
            self._interval: float | None = None
        else:
            self._interval = float(checkpoint_interval_s)
        self._save_lock = threading.Lock()
        self._meta_lock = threading.Lock()
        self._next_seq = 1
        self._durable_seq = 0
        self._inflight: Future | None = None
        self._last_periodic_at = self._clock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._closed = False

    def periodic_enabled(self) -> bool:
        return self._interval is not None

    def should_schedule_periodic(self) -> bool:
        if not self.periodic_enabled():
            return False
        with self._meta_lock:
            if self._inflight is not None and not self._inflight.done():
                return False
        assert self._interval is not None
        return self._clock() - self._last_periodic_at >= self._interval

    def request_periodic(self, tracks: dict) -> None:
        with self._meta_lock:
            if not self.periodic_enabled():
                return
            if self._inflight is not None and not self._inflight.done():
                return
            seq = self._next_seq
            self._next_seq += 1
            self._inflight = self._executor.submit(self._run_periodic, seq, tracks)

    def _run_periodic(self, seq: int, tracks: dict) -> None:
        try:
            self._persist_seq(seq, tracks)
        except Exception:
            return

    def _persist_seq(self, seq: int, tracks: dict) -> None:
        if self._before_persist is not None:
            self._before_persist()
        with self._save_lock:
            if seq <= self._durable_seq:
                return
            self._persist_locked(seq, tracks)

    def _persist_locked(self, seq: int, tracks: dict) -> None:
        t0 = self._clock()
        self._save_tracks(tracks, self._library_dir)
        duration = self._clock() - t0
        self._durable_seq = seq
        if self._interval is not None:
            self._interval = max(10.0, 10.0 * duration)
        self._last_periodic_at = self._clock()

    def _save_sync(self, tracks: dict) -> None:
        with self._meta_lock:
            seq = self._next_seq
            self._next_seq += 1
        self._persist_seq(seq, tracks)

    def save_urgent(self, tracks: dict) -> None:
        self._save_sync(tracks)

    def save_final(self, tracks: dict) -> None:
        self.join()
        self._save_sync(tracks)

    def join(self) -> None:
        with self._meta_lock:
            fut = self._inflight
        if fut is None:
            return
        try:
            fut.result()
        except Exception:
            return

    def close(self) -> None:
        """Wait for inflight periodics and release the worker thread."""
        if self._closed:
            return
        self._closed = True
        self.join()
        self._executor.shutdown(wait=False)


class ManifestCheckpoint:
    """Mark completes and schedule durable snapshots via ManifestPersister."""

    def __init__(
        self,
        plan: Plan,
        pending: set[tuple[str, str]],
        *,
        persister: ManifestPersister,
    ) -> None:
        self.plan = plan
        self.pending = pending
        self.persister = persister

    def mark_item_complete(
        self, item: PlannedTrack, *, metadata: str | None = None
    ) -> set[tuple[str, str]] | None:
        """Mark complete under the caller's stats_lock.

        Returns a pending-key copy when a periodic should be scheduled after the
        caller releases stats_lock; otherwise None.
        """
        if self.plan.manifest is None:
            return None
        fmt = coerce_output_format(item.output_format)
        record = self.plan.manifest.tracks.get(source_key(item.source_path), {}).get(
            fmt
        )
        if record is None:
            return None
        mark_complete(
            record,
            source=source_signature(item.source_path),
            metadata=metadata if metadata is not None else metadata_signature(item.source_el),
            output=output_signature(item.dest_path),
            recipe=recipe_from_item(item),
        )
        self.pending.discard((source_key(item.source_path), fmt))
        if not self.persister.should_schedule_periodic():
            return None
        return set(self.pending)

    def schedule_periodic(self, pending_copy: set[tuple[str, str]]) -> None:
        """Build overlay and request periodic; call outside stats_lock."""
        if self.plan.manifest is None:
            return
        snapshot = _overlay_tracks(self.plan.manifest, pending_copy)
        self.persister.request_periodic(snapshot)

    def take_urgent_snapshot_keys(self, item: PlannedTrack) -> set[tuple[str, str]] | None:
        """Discard pending for item; return pending keys for overlay (no I/O)."""
        fmt = coerce_output_format(item.output_format)
        self.pending.discard((source_key(item.source_path), fmt))
        if self.plan.manifest is None:
            return None
        return set(self.pending)

    def persist_urgent(self, snapshot: dict | None) -> None:
        """Persist an urgent snapshot outside stats_lock."""
        if snapshot is None:
            return
        try:
            self.persister.save_urgent(snapshot)
        except Exception as exc:
            raise ManifestPersistError(str(exc)) from exc


def _urgent_persist_after_conflict(
    ctx: "ExecuteContext",
    item: PlannedTrack,
    *,
    outcome_bucket: Literal["conflicts", "state_changed"],
) -> None:
    """Record conflict/state_changed under stats_lock; persist outside it."""
    name = item.dest_name
    with ctx.stats_lock:
        pending_keys = ctx.checkpoint.take_urgent_snapshot_keys(item)
        if outcome_bucket == "conflicts":
            ctx.stats.conflicts.append(name)
        else:
            ctx.stats.state_changed.append(name)
        manifest = ctx.plan.manifest
    if pending_keys is None or manifest is None:
        return
    snapshot = _overlay_tracks(manifest, pending_keys)
    ctx.checkpoint.persist_urgent(snapshot)


def _maybe_schedule_periodic(
    ctx: "ExecuteContext", pending_copy: set[tuple[str, str]] | None
) -> None:
    if pending_copy is not None:
        ctx.checkpoint.schedule_periodic(pending_copy)


def _commit_refresh_xml_freshness(
    prepared: PreparedConversion,
    decisions: dict[tuple[str, str], Decision],
    succeeded: set[tuple[str, str]],
    pending: set[tuple[str, str]],
    persister: ManifestPersister,
) -> None:
    """Advance metadata signatures only after Import XML has been written."""
    changed = False
    for item in prepared.items:
        fmt = coerce_output_format(item.output_format)
        key = (source_key(item.source_path), fmt)
        decision = decisions.get(key)
        if decision is None or decision.action not in {
            "refresh_xml",
            "update_metadata",
        }:
            continue
        if key not in succeeded:
            continue
        record = prepared.manifest.tracks.get(key[0], {}).get(fmt)
        if record is None:
            continue
        mark_complete(
            record,
            source=source_signature(item.source_path),
            metadata=metadata_signature(item.source_el),
            output=output_signature(item.dest_path),
            recipe=recipe_from_item(item),
        )
        changed = True
    if changed:
        persister.save_urgent(_overlay_tracks(prepared.manifest, pending))


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
    reason: str = "",
) -> ItemResult:
    return ItemResult(
        source=item.source_path,
        destination=item.dest_path,
        action=action,
        outcome=outcome,
        error=error,
        write=write,
        reason=reason,
    )


def _snapshot_blocks_write(
    item: PlannedTrack, decision: Decision, ctx: ExecuteContext
) -> ItemResult | None:
    """Return a blocking result if frozen stats no longer match."""
    name = item.dest_name
    key_src = decision.source_stat
    if key_src is not None:
        current_src = file_snapshot(item.source_path)
        if current_src is None or not snapshots_match(key_src, current_src):
            _urgent_persist_after_conflict(
                ctx, item, outcome_bucket="state_changed"
            )
            ctx.finish("state_changed", name)
            return _item_result(
                item, decision.action, "state_changed", reason=decision.reason
            )
    frozen_dest = decision.dest_stat
    current_dest = file_snapshot(item.dest_path)
    dest_changed = (frozen_dest is None and current_dest is not None) or (
        frozen_dest is not None and not snapshots_match(frozen_dest, current_dest)
    )
    if dest_changed:
        _urgent_persist_after_conflict(ctx, item, outcome_bucket="conflicts")
        ctx.finish("conflict", name)
        return _item_result(
            item, decision.action, "conflict", reason=decision.reason
        )
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
    else:
        decision = classify_item(ctx.plan, item, ctx.force)
    action = decision.action
    reason = decision.reason

    def result(
        outcome: str,
        *,
        error: str | None = None,
        write: str = "",
        action_name: str | None = None,
    ) -> ItemResult:
        return _item_result(
            item,
            action if action_name is None else action_name,
            outcome,
            error=error,
            write=write,
            reason=reason,
        )

    if action in {"reuse", "in_place_noop", "refresh_xml"}:
        with ctx.stats_lock:
            ctx.stats.skipped += 1
        ctx.mark_succeeded(item)
        ctx.finish("skip", name)
        return result("succeeded")
    if action == "external_modification_conflict":
        _urgent_persist_after_conflict(ctx, item, outcome_bucket="conflicts")
        ctx.finish("conflict", name)
        return result("conflict")
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
                fmt = coerce_output_format(item.output_format)
                record = (
                    ctx.plan.manifest.tracks.get(source_key(item.source_path), {}).get(
                        fmt
                    )
                    if ctx.plan.manifest is not None
                    else None
                )
                old_meta = (record or {}).get("metadata", {}).get("signature")
                pending_copy = ctx.checkpoint.mark_item_complete(
                    item, metadata=old_meta
                )
            _maybe_schedule_periodic(ctx, pending_copy)
            ctx.mark_succeeded(item)
            ctx.finish("copy", name)
            return result("succeeded", write="copy")
        if action == "rewrite_container" and not is_aiff:
            pcm_src = _pcm_origin_for_container_rewrite(ctx.plan, item, ctx.force)
            rewrite_failed = False
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
                rewrite_failed = True
            if rewrite_failed:
                _urgent_persist_after_conflict(
                    ctx, item, outcome_bucket="state_changed"
                )
                ctx.finish("state_changed", name)
                return result("state_changed", action_name="rewrite_container")
            with ctx.stats_lock:
                ctx.stats.copied += 1
                pending_copy = ctx.checkpoint.mark_item_complete(item)
            _maybe_schedule_periodic(ctx, pending_copy)
            ctx.mark_succeeded(item)
            ctx.finish("copy", name)
            return result(
                "succeeded", write="copy", action_name="rewrite_container"
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

            rewrite_failed = False
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
                rewrite_failed = True
            if rewrite_failed:
                _urgent_persist_after_conflict(
                    ctx, item, outcome_bucket="state_changed"
                )
                ctx.finish("state_changed", name)
                return result("state_changed", action_name="rewrite_container")
            with ctx.stats_lock:
                ctx.stats.copied += 1
                pending_copy = ctx.checkpoint.mark_item_complete(item)
            _maybe_schedule_periodic(ctx, pending_copy)
            ctx.mark_succeeded(item)
            ctx.finish("copy", name)
            return result(
                "succeeded", write="copy", action_name="rewrite_container"
            )
        copy_pcm = action == "recreate_missing" and item.passthrough
        encode = action in {"transcode", "recreate_missing"} and not copy_pcm
        if copy_pcm and is_aiff:
            plan_module.write_aiff_output(
                item.source_path,
                item.dest_path,
                item.source_el,
                passthrough=True,
                codec=item.codec,
                bit_depth=item.bit_depth,
                sample_rate=item.sample_rate,
                cover_cache=ctx.plan.cover_cache,
                cancel_event=ctx.cancel_event,
                cover_lock=ctx.cover_lock,
            )
            with ctx.stats_lock:
                ctx.stats.copied += 1
                pending_copy = ctx.checkpoint.mark_item_complete(item)
            _maybe_schedule_periodic(ctx, pending_copy)
            ctx.mark_succeeded(item)
            ctx.finish("copy", name)
            return result("succeeded", write="copy")
        if copy_pcm:
            copy_wav_atomic(
                item.source_path,
                item.dest_path,
                bit_depth=item.bit_depth,
                sample_rate=item.sample_rate,
                cancel_event=ctx.cancel_event,
            )
            with ctx.stats_lock:
                ctx.stats.copied += 1
                pending_copy = ctx.checkpoint.mark_item_complete(item)
            _maybe_schedule_periodic(ctx, pending_copy)
            ctx.mark_succeeded(item)
            ctx.finish("copy", name)
            return result("succeeded", write="copy")
        if encode and is_aiff:
            plan_module.write_aiff_output(
                item.source_path,
                item.dest_path,
                item.source_el,
                passthrough=False,
                codec=item.codec or format_policy.pcm_codec_for_depth(
                    item.bit_depth, output_format="aiff"
                ),
                bit_depth=item.bit_depth,
                sample_rate=item.sample_rate,
                cover_cache=ctx.plan.cover_cache,
                cancel_event=ctx.cancel_event,
                cover_lock=ctx.cover_lock,
            )
            with ctx.stats_lock:
                ctx.stats.converted += 1
                pending_copy = ctx.checkpoint.mark_item_complete(item)
            _maybe_schedule_periodic(ctx, pending_copy)
            ctx.mark_succeeded(item)
            ctx.finish("convert", name)
            return result("succeeded", write="transcode")
        if encode:
            codec = item.codec or format_policy.pcm_codec_for_depth(
                item.bit_depth, output_format=item.output_format
            )
            plan_module.run_ffmpeg(
                item.source_path,
                item.dest_path,
                codec,
                force=True,
                sample_rate=item.sample_rate,
                bit_depth=item.bit_depth,
                cancel_event=ctx.cancel_event,
                output_format=item.output_format,
            )
            with ctx.stats_lock:
                ctx.stats.converted += 1
                pending_copy = ctx.checkpoint.mark_item_complete(item)
            _maybe_schedule_periodic(ctx, pending_copy)
            ctx.mark_succeeded(item)
            ctx.finish("convert", name)
            return result("succeeded", write="transcode")
        raise CliError(f"unsupported convert action {action!r} for {item.source_path}")
    except ManifestPersistError:
        raise
    except CancelledError:
        return result("cancelled")
    except Exception as exc:  # noqa: BLE001 — collect all; report after pool
        with ctx.stats_lock:
            ctx.stats.errors.append(str(exc))
        ctx.finish("error", name)
        return result("failed", error=str(exc))


def convert_unique(
    plan: Plan,
    force: bool,
    *,
    progress: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    items: list[PlannedTrack] | None = None,
    workers: int | None = None,
    checkpoint_interval_s: float | None = None,
    clock: Callable[[], float] | None = None,
    persister: ManifestPersister | None = None,
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
    owns_persister = persister is None
    if owns_persister:
        persister = ManifestPersister(
            plan.library_dir,
            checkpoint_interval_s=checkpoint_interval_s,
            clock=clock,
        )
    checkpoint = ManifestCheckpoint(plan, pending, persister=persister)

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
        if owns_persister:
            persister.close()
        else:
            persister.join()
    apply_item_result_aggregates(stats)
    return stats


def execute_prepared(
    prepared: PreparedConversion,
    *,
    force: bool = False,
    progress: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    workers: int | None = None,
    checkpoint_interval_s: float | None = 10.0,
    clock: Callable[[], float] | None = None,
) -> ConvertStats:
    """Save manifest, convert unique items, apply XML per plan, write import XML.

    Encode cancel waits in-flight; successes still receive apply_xml + write.
    Hosts map cancel vs errors vs ok from the returned stats and cancel_event.
    """
    if prepared.reservation is not None:
        prepared.reservation.refresh_inventory()
    decisions = _decisions_for_prepared(prepared, force)
    prepared.decisions = dict(decisions)
    pending = _mutating_assignment_keys(prepared, force, decisions)
    converter_manifest.save_manifest_tracks(
        _overlay_tracks(prepared.manifest, pending),
        prepared.library_dir,
    )
    persister = ManifestPersister(
        prepared.library_dir,
        checkpoint_interval_s=checkpoint_interval_s,
        clock=clock,
    )
    try:
        plans = prepared.plans
        stats = convert_unique(
            plans[0],
            force=force,
            progress=progress,
            on_progress=on_progress,
            cancel_event=cancel_event,
            items=prepared.items,
            workers=workers,
            persister=persister,
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
                    reason=result.reason,
                )
                for result in stats.item_results
            ]
        try:
            persister.save_final(_overlay_tracks(prepared.manifest, pending))
        except Exception as exc:
            raise ManifestPersistError(str(exc)) from exc
        appended_by_plan: list[int] = []
        playlist_results: list = []
        for one_plan in plans:
            result = xml_output.apply_xml(one_plan, stats.succeeded)
            playlist_results.append(result)
            appended_by_plan.append(result.appended)
        xml_output.write_import_xml(plans[0].output_root, plans[0].output)
        _commit_refresh_xml_freshness(
            prepared, decisions, stats.succeeded, pending, persister
        )
        stats.appended_by_plan = appended_by_plan
        stats.appended = sum(appended_by_plan)
        stats.playlist_results = playlist_results
        return stats
    finally:
        persister.close()
